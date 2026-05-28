"""Part-aware deterministic compaction for EFP Runtime v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from ..session.models import CompactionPart, Message, MessagePart, MessagePartType


@dataclass(frozen=True)
class CompactionResult:
    """Result of applying a compaction strategy."""

    messages: list[Message]
    compacted_part_count: int = 0
    compacted_message_count: int = 0
    compacted_tool_pair_count: int = 0

    @property
    def compacted(self) -> bool:
        return self.compacted_part_count > 0


@dataclass(frozen=True)
class _PartRef:
    key: tuple[int, int]
    message_index: int
    part_index: int
    message: Message
    part: MessagePart


@dataclass(frozen=True)
class _Block:
    refs: list[_PartRef]
    is_tool_pair: bool = False

    @property
    def part_count(self) -> int:
        return len(self.refs)


class PartAwareCompactionStrategy:
    """Keep recent part blocks and summarize older blocks deterministically."""

    def __init__(self, *, max_parts: int = 40):
        if max_parts < 1:
            raise ValueError("max_parts must be at least 1")
        self.max_parts = max_parts

    def compact(self, messages: list[Message]) -> CompactionResult:
        refs = _flatten_messages(messages)
        if len(refs) <= self.max_parts:
            return CompactionResult(messages=list(messages))

        blocks = _group_part_blocks(refs)
        kept_blocks = self._select_recent_blocks(blocks)
        kept_keys = {ref.key for block in kept_blocks for ref in block.refs}
        compacted_blocks = [
            block for block in blocks if not any(ref.key in kept_keys for ref in block.refs)
        ]
        if not compacted_blocks:
            return CompactionResult(messages=list(messages))

        compaction_message = _build_compaction_message(compacted_blocks)
        remaining_messages = _rebuild_messages(messages, kept_keys)
        return CompactionResult(
            messages=[compaction_message, *remaining_messages],
            compacted_part_count=sum(block.part_count for block in compacted_blocks),
            compacted_message_count=len(
                {ref.message_index for block in compacted_blocks for ref in block.refs}
            ),
            compacted_tool_pair_count=sum(1 for block in compacted_blocks if block.is_tool_pair),
        )

    def _select_recent_blocks(self, blocks: list[_Block]) -> list[_Block]:
        kept_reversed: list[_Block] = []
        used_parts = 1
        for block in reversed(blocks):
            if used_parts + block.part_count > self.max_parts:
                break
            kept_reversed.append(block)
            used_parts += block.part_count
        return list(reversed(kept_reversed))


def _flatten_messages(messages: list[Message]) -> list[_PartRef]:
    refs: list[_PartRef] = []
    for message_index, message in enumerate(messages):
        for part_index, part in enumerate(message.parts):
            refs.append(
                _PartRef(
                    key=(message_index, part_index),
                    message_index=message_index,
                    part_index=part_index,
                    message=message,
                    part=part,
                )
            )
    return refs


def _group_part_blocks(refs: list[_PartRef]) -> list[_Block]:
    calls_by_id: dict[str, _PartRef] = {}
    results_by_id: dict[str, _PartRef] = {}
    for ref in refs:
        if ref.part.type is MessagePartType.TOOL_CALL and ref.part.tool_call is not None:
            calls_by_id.setdefault(ref.part.tool_call.call_id, ref)
        elif ref.part.type is MessagePartType.TOOL_RESULT and ref.part.tool_result is not None:
            results_by_id.setdefault(ref.part.tool_result.call_id, ref)

    used_keys: set[tuple[int, int]] = set()
    blocks: list[_Block] = []
    for ref in refs:
        if ref.key in used_keys:
            continue

        call_id = _tool_pair_call_id(ref.part)
        if call_id is not None and call_id in calls_by_id and call_id in results_by_id:
            pair_refs = sorted([calls_by_id[call_id], results_by_id[call_id]], key=lambda item: item.key)
            if not any(pair_ref.key in used_keys for pair_ref in pair_refs):
                blocks.append(_Block(refs=pair_refs, is_tool_pair=True))
                used_keys.update(pair_ref.key for pair_ref in pair_refs)
                continue

        blocks.append(_Block(refs=[ref], is_tool_pair=False))
        used_keys.add(ref.key)
    return blocks


def _tool_pair_call_id(part: MessagePart) -> str | None:
    if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
        return part.tool_call.call_id
    if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
        return part.tool_result.call_id
    return None


def _build_compaction_message(compacted_blocks: list[_Block]) -> Message:
    part_count = sum(block.part_count for block in compacted_blocks)
    message_refs = {ref.message_index: ref.message for block in compacted_blocks for ref in block.refs}
    message_count = len(message_refs)
    tool_pair_count = sum(1 for block in compacted_blocks if block.is_tool_pair)
    source_message_ids = [message.message_id for _, message in sorted(message_refs.items())]
    session_id = next(iter(message_refs.values())).session_id if message_refs else ""
    summary = (
        f"Compacted {part_count} message part(s) from {message_count} message(s). "
        f"Tool call/result pair(s) compacted: {tool_pair_count}."
    )
    compaction = CompactionPart(
        summary=summary,
        source_message_ids=source_message_ids,
        auto=True,
        original_part_count=part_count,
        original_message_count=message_count,
        tool_pair_count=tool_pair_count,
    )
    return Message(
        role="system",
        session_id=session_id,
        parts=[MessagePart.compaction_part(compaction)],
        status="complete",
    )


def _rebuild_messages(messages: list[Message], kept_keys: set[tuple[int, int]]) -> list[Message]:
    rebuilt: list[Message] = []
    for message_index, message in enumerate(messages):
        parts = [
            deepcopy(part)
            for part_index, part in enumerate(message.parts)
            if (message_index, part_index) in kept_keys
        ]
        if not parts:
            continue
        rebuilt.append(
            Message(
                role=message.role,
                session_id=message.session_id,
                message_id=message.message_id,
                parts=parts,
                parent_message_id=message.parent_message_id,
                metadata=dict(message.metadata),
                status=message.status,
                usage=dict(message.usage),
                created_at=message.created_at,
                completed_at=message.completed_at,
            )
        )
    return rebuilt
