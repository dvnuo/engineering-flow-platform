"""Part-aware deterministic compaction for EFP Runtime v2."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Mapping

from .summary import latest_compaction_summary, render_anchored_compaction_summary
from ..session.models import (
    CompactionPart,
    Message,
    MessagePart,
    MessagePartType,
    MessageRole,
)


@dataclass(frozen=True)
class ContextBudget:
    """Approximate request context budget."""

    max_parts: int | None = None
    max_chars: int | None = None
    reserve_chars: int = 0

    def __post_init__(self) -> None:
        if self.max_parts is not None and self.max_parts < 1:
            raise ValueError("max_parts must be at least 1")
        if self.max_chars is not None and self.max_chars < 1:
            raise ValueError("max_chars must be at least 1")
        if self.reserve_chars < 0:
            raise ValueError("reserve_chars must be at least 0")

    @property
    def effective_max_chars(self) -> int | None:
        if self.max_chars is None:
            return None
        return max(0, self.max_chars - self.reserve_chars)


@dataclass(frozen=True)
class CompactionResult:
    """Result of applying a compaction strategy."""

    messages: list[Message]
    compacted_part_count: int = 0
    compacted_message_count: int = 0
    compacted_tool_pair_count: int = 0
    compacted_chars: int = 0
    kept_chars: int = 0
    source_messages: list[Message] = field(default_factory=list)
    compacted_messages: list[Message] = field(default_factory=list)
    kept_messages: list[Message] = field(default_factory=list)

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

    @property
    def char_count(self) -> int:
        return sum(_part_chars(ref.part) for ref in self.refs)


@dataclass(frozen=True)
class _Turn:
    start_index: int
    end_index: int
    message_id: str


class BudgetCompactionStrategy:
    """Keep protected and recent part blocks inside an approximate budget."""

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        max_parts: int | None = None,
        max_chars: int | None = None,
        reserve_chars: int = 0,
    ):
        if budget is not None and (
            max_parts is not None or max_chars is not None or reserve_chars != 0
        ):
            raise ValueError("pass either budget or max_parts/max_chars/reserve_chars")
        self.budget = budget or ContextBudget(
            max_parts=max_parts,
            max_chars=max_chars,
            reserve_chars=reserve_chars,
        )
        self.max_parts = self.budget.max_parts
        self.max_chars = self.budget.max_chars
        self.reserve_chars = self.budget.reserve_chars

    def compact(self, messages: list[Message]) -> CompactionResult:
        refs = _flatten_messages(messages)
        total_chars = _messages_chars(messages)
        if not self._over_budget(part_count=len(refs), char_count=total_chars):
            return CompactionResult(
                messages=list(messages),
                source_messages=list(messages),
                kept_messages=list(messages),
                kept_chars=total_chars,
            )

        blocks = _group_part_blocks(refs)
        pending_call_ids = _pending_tool_call_ids(refs)
        kept_blocks = self._select_recent_blocks(blocks, pending_call_ids=pending_call_ids)
        kept_keys = {ref.key for block in kept_blocks for ref in block.refs}
        compacted_blocks = [
            block for block in blocks if not any(ref.key in kept_keys for ref in block.refs)
        ]
        if not compacted_blocks:
            return CompactionResult(
                messages=list(messages),
                source_messages=list(messages),
                kept_messages=list(messages),
                kept_chars=total_chars,
            )

        compacted_keys = {ref.key for block in compacted_blocks for ref in block.refs}
        compaction_message = _build_compaction_message(compacted_blocks)
        remaining_items = _rebuild_message_items(messages, kept_keys)
        final_messages = _insert_compaction_message(
            compaction_message,
            remaining_items,
            compacted_blocks,
        )
        return CompactionResult(
            messages=final_messages,
            source_messages=list(messages),
            compacted_messages=_rebuild_messages(messages, compacted_keys),
            kept_messages=_rebuild_messages(messages, kept_keys),
            compacted_part_count=sum(block.part_count for block in compacted_blocks),
            compacted_message_count=len(
                {ref.message_index for block in compacted_blocks for ref in block.refs}
            ),
            compacted_tool_pair_count=sum(1 for block in compacted_blocks if block.is_tool_pair),
            compacted_chars=sum(block.char_count for block in compacted_blocks),
            kept_chars=_messages_chars(final_messages),
        )

    def _over_budget(self, *, part_count: int, char_count: int) -> bool:
        if self.budget.max_parts is not None and part_count > self.budget.max_parts:
            return True
        effective_max_chars = self.budget.effective_max_chars
        if effective_max_chars is not None and char_count > effective_max_chars:
            return True
        return False

    def _select_recent_blocks(
        self,
        blocks: list[_Block],
        *,
        pending_call_ids: set[str],
    ) -> list[_Block]:
        protected_indices = {
            index
            for index, block in enumerate(blocks)
            if _is_protected_block(block, pending_call_ids=pending_call_ids)
        }
        kept_indices = set(protected_indices)
        latest_unprotected_index = _latest_unprotected_block_index(
            blocks,
            protected_indices=protected_indices,
        )
        if latest_unprotected_index is not None:
            kept_indices.add(latest_unprotected_index)

        for index in reversed(range(len(blocks))):
            if index in kept_indices:
                continue
            candidate_indices = {*kept_indices, index}
            candidate_parts, candidate_chars = _selection_usage(blocks, candidate_indices)
            if not self._fits_budget(part_count=candidate_parts, char_count=candidate_chars):
                continue
            kept_indices.add(index)

        return [blocks[index] for index in sorted(kept_indices)]

    def _fits_budget(self, *, part_count: int, char_count: int) -> bool:
        if self.budget.max_parts is not None and part_count > self.budget.max_parts:
            return False
        effective_max_chars = self.budget.effective_max_chars
        if effective_max_chars is not None and char_count > effective_max_chars:
            return False
        return True


class PartAwareCompactionStrategy(BudgetCompactionStrategy):
    """Keep recent part blocks and summarize older blocks deterministically."""

    def __init__(self, *, max_parts: int = 40):
        super().__init__(budget=ContextBudget(max_parts=max_parts))


class TailTurnCompactionStrategy(BudgetCompactionStrategy):
    """Keep a recent suffix of user turns and summarize older history."""

    def __init__(
        self,
        *,
        budget: ContextBudget | None = None,
        max_parts: int | None = None,
        max_chars: int | None = None,
        reserve_chars: int = 0,
        tail_turns: int = 2,
        preserve_recent_chars: int | None = None,
    ):
        _validate_non_negative_int(tail_turns, "tail_turns")
        if preserve_recent_chars is not None:
            _validate_non_negative_int(
                preserve_recent_chars,
                "preserve_recent_chars",
            )
        super().__init__(
            budget=budget,
            max_parts=max_parts,
            max_chars=max_chars,
            reserve_chars=reserve_chars,
        )
        self.tail_turns = tail_turns
        self.preserve_recent_chars = preserve_recent_chars

    def compact(self, messages: list[Message]) -> CompactionResult:
        refs = _flatten_messages(messages)
        total_chars = _messages_chars(messages)
        if not self._over_budget(part_count=len(refs), char_count=total_chars):
            return CompactionResult(
                messages=list(messages),
                source_messages=list(messages),
                kept_messages=list(messages),
                kept_chars=total_chars,
            )

        blocks = _group_part_blocks(refs)
        pending_call_ids = _pending_tool_call_ids(refs)
        protected_indices = {
            index
            for index, block in enumerate(blocks)
            if _is_protected_block(block, pending_call_ids=pending_call_ids)
        }
        recent_budget = self._recent_tail_budget()
        tail_start_index = self._tail_start_index(messages, recent_budget)
        tail_indices = (
            _block_indices_from_message_index(blocks, tail_start_index)
            if tail_start_index is not None
            else set()
        )
        kept_indices = protected_indices | tail_indices
        compacted_blocks = [
            block for index, block in enumerate(blocks) if index not in kept_indices
        ]
        if not compacted_blocks:
            return CompactionResult(
                messages=list(messages),
                source_messages=list(messages),
                kept_messages=list(messages),
                kept_chars=total_chars,
            )

        kept_keys = {ref.key for index in kept_indices for ref in blocks[index].refs}
        compacted_keys = {ref.key for block in compacted_blocks for ref in block.refs}
        tail_start_message_id = _tail_start_message_id(
            messages,
            blocks=blocks,
            tail_indices=tail_indices,
            tail_start_index=tail_start_index,
        )
        compaction_message = _build_compaction_message(
            compacted_blocks,
            previous_summary=latest_compaction_summary(messages),
            tail_start_message_id=tail_start_message_id,
            metadata={
                "strategy": "tail_turn",
                "tail_turns": self.tail_turns,
                "preserve_recent_chars": recent_budget,
                "tail_start_message_id": tail_start_message_id,
            },
        )
        remaining_items = _rebuild_message_items(messages, kept_keys)
        final_messages = _insert_compaction_message(
            compaction_message,
            remaining_items,
            compacted_blocks,
        )
        return CompactionResult(
            messages=final_messages,
            source_messages=list(messages),
            compacted_messages=_rebuild_messages(messages, compacted_keys),
            kept_messages=_rebuild_messages(messages, kept_keys),
            compacted_part_count=sum(block.part_count for block in compacted_blocks),
            compacted_message_count=len(
                {ref.message_index for block in compacted_blocks for ref in block.refs}
            ),
            compacted_tool_pair_count=sum(1 for block in compacted_blocks if block.is_tool_pair),
            compacted_chars=sum(block.char_count for block in compacted_blocks),
            kept_chars=_messages_chars(final_messages),
        )

    def _recent_tail_budget(self) -> int:
        if self.preserve_recent_chars is not None:
            return self.preserve_recent_chars
        effective_max_chars = self.budget.effective_max_chars
        if effective_max_chars is None:
            return 8000
        return max(2000, min(8000, effective_max_chars // 4))

    def _tail_start_index(
        self,
        messages: list[Message],
        recent_budget: int,
    ) -> int | None:
        if self.tail_turns <= 0:
            return None
        turns = _user_turns(messages)
        if not turns:
            return None

        recent_turns = turns[-self.tail_turns :]
        turn_sizes = [
            _messages_chars(messages[turn.start_index : turn.end_index])
            for turn in recent_turns
        ]
        total = 0
        keep_start: int | None = None
        for index in reversed(range(len(recent_turns))):
            turn = recent_turns[index]
            turn_size = turn_sizes[index]
            if total + turn_size <= recent_budget:
                total += turn_size
                keep_start = turn.start_index
                continue
            split_start = _split_turn_start(
                messages,
                turn=turn,
                budget=recent_budget - total,
            )
            if split_start is not None:
                keep_start = split_start
            break
        return keep_start


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


def _pending_tool_call_ids(refs: list[_PartRef]) -> set[str]:
    call_ids = {
        ref.part.tool_call.call_id
        for ref in refs
        if ref.part.type is MessagePartType.TOOL_CALL and ref.part.tool_call is not None
    }
    result_ids = {
        ref.part.tool_result.call_id
        for ref in refs
        if ref.part.type is MessagePartType.TOOL_RESULT and ref.part.tool_result is not None
    }
    return call_ids - result_ids


def _user_turns(messages: list[Message]) -> list[_Turn]:
    turns: list[_Turn] = []
    for index, message in enumerate(messages):
        if message.role is not MessageRole.USER:
            continue
        if _message_has_compaction_part(message):
            continue
        turns.append(
            _Turn(
                start_index=index,
                end_index=len(messages),
                message_id=message.message_id,
            )
        )

    for index in range(len(turns) - 1):
        turns[index] = _Turn(
            start_index=turns[index].start_index,
            end_index=turns[index + 1].start_index,
            message_id=turns[index].message_id,
        )
    return turns


def _message_has_compaction_part(message: Message) -> bool:
    return any(part.type is MessagePartType.COMPACTION for part in message.parts)


def _split_turn_start(
    messages: list[Message],
    *,
    turn: _Turn,
    budget: int,
) -> int | None:
    if budget <= 0:
        return None
    if turn.end_index - turn.start_index <= 1:
        return None
    for start_index in range(turn.start_index + 1, turn.end_index):
        if _messages_chars(messages[start_index : turn.end_index]) <= budget:
            return start_index
    return None


def _block_indices_from_message_index(
    blocks: list[_Block],
    message_index: int,
) -> set[int]:
    return {
        index
        for index, block in enumerate(blocks)
        if any(ref.message_index >= message_index for ref in block.refs)
    }


def _tail_start_message_id(
    messages: list[Message],
    *,
    blocks: list[_Block],
    tail_indices: set[int],
    tail_start_index: int | None,
) -> str | None:
    if tail_start_index is None:
        return None
    message_indices = [
        ref.message_index
        for index in tail_indices
        for ref in blocks[index].refs
        if ref.message_index >= tail_start_index
    ]
    if not message_indices:
        return None
    return messages[min(message_indices)].message_id


def _is_protected_block(block: _Block, *, pending_call_ids: set[str]) -> bool:
    for ref in block.refs:
        if (
            ref.message.role is MessageRole.SYSTEM
            and ref.part.type is not MessagePartType.COMPACTION
        ):
            return True
        if (
            ref.part.type is MessagePartType.TOOL_CALL
            and ref.part.tool_call is not None
            and ref.part.tool_call.call_id in pending_call_ids
        ):
            return True
    return False


def _latest_unprotected_block_index(
    blocks: list[_Block],
    *,
    protected_indices: set[int],
) -> int | None:
    for index in reversed(range(len(blocks))):
        if index not in protected_indices:
            return index
    return None


def _tail_turn_block_indices(blocks: list[_Block], *, tail_turns: int) -> set[int]:
    if tail_turns <= 0:
        return set()

    user_message_indices = sorted(
        {
            ref.message_index
            for block in blocks
            for ref in block.refs
            if ref.message.role is MessageRole.USER
        }
    )
    if not user_message_indices:
        return set()

    tail_start_index = user_message_indices[
        max(0, len(user_message_indices) - tail_turns)
    ]
    return {
        index
        for index, block in enumerate(blocks)
        if any(ref.message_index >= tail_start_index for ref in block.refs)
    }


def _recent_char_block_indices(
    blocks: list[_Block],
    *,
    preserve_recent_chars: int | None,
) -> set[int]:
    if preserve_recent_chars is None or preserve_recent_chars <= 0:
        return set()

    selected: set[int] = set()
    chars = 0
    for index in reversed(range(len(blocks))):
        selected.add(index)
        chars += blocks[index].char_count
        if chars >= preserve_recent_chars:
            break
    return selected


def _selection_usage(blocks: list[_Block], kept_indices: set[int]) -> tuple[int, int]:
    kept_blocks = [block for index, block in enumerate(blocks) if index in kept_indices]
    compacted_blocks = [
        block for index, block in enumerate(blocks) if index not in kept_indices
    ]
    part_count = sum(block.part_count for block in kept_blocks)
    char_count = sum(block.char_count for block in kept_blocks)
    if compacted_blocks:
        part_count += 1
        char_count += _compaction_summary_chars(compacted_blocks)
    return part_count, char_count


def _build_compaction_message(
    compacted_blocks: list[_Block],
    *,
    previous_summary: str | None = None,
    tail_start_message_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Message:
    part_count = sum(block.part_count for block in compacted_blocks)
    message_refs = {ref.message_index: ref.message for block in compacted_blocks for ref in block.refs}
    message_count = len(message_refs)
    tool_pair_count = sum(1 for block in compacted_blocks if block.is_tool_pair)
    source_messages = [message for _, message in sorted(message_refs.items())]
    source_message_ids = [message.message_id for message in source_messages]
    session_id = next(iter(message_refs.values())).session_id if message_refs else ""
    summary = render_anchored_compaction_summary(
        session_id=session_id,
        compacted_messages=source_messages,
        previous_summary=previous_summary or latest_compaction_summary(source_messages),
        metadata={
            "compacted_part_count": part_count,
            "compacted_message_count": message_count,
            "compacted_tool_pair_count": tool_pair_count,
        },
    )
    compaction_metadata = {
        "approx_compacted_chars": sum(block.char_count for block in compacted_blocks)
    }
    compaction_metadata.update(dict(metadata or {}))
    compaction = CompactionPart(
        summary=summary,
        source_message_ids=source_message_ids,
        auto=True,
        tail_start_message_id=tail_start_message_id,
        original_part_count=part_count,
        original_message_count=message_count,
        tool_pair_count=tool_pair_count,
        metadata=compaction_metadata,
    )
    return Message(
        role="system",
        session_id=session_id,
        parts=[MessagePart.compaction_part(compaction)],
        status="complete",
    )


def _rebuild_messages(messages: list[Message], kept_keys: set[tuple[int, int]]) -> list[Message]:
    return [message for _, message in _rebuild_message_items(messages, kept_keys)]


def _rebuild_message_items(
    messages: list[Message],
    kept_keys: set[tuple[int, int]],
) -> list[tuple[int, Message]]:
    rebuilt_items: list[tuple[int, Message]] = []
    for message_index, message in enumerate(messages):
        parts = [
            deepcopy(part)
            for part_index, part in enumerate(message.parts)
            if (message_index, part_index) in kept_keys
        ]
        if not parts:
            continue
        rebuilt_message = Message(
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
        rebuilt_items.append((message_index, rebuilt_message))
    return rebuilt_items


def _insert_compaction_message(
    compaction_message: Message,
    remaining_items: list[tuple[int, Message]],
    compacted_blocks: list[_Block],
) -> list[Message]:
    first_compacted_message_index = min(
        ref.message_index for block in compacted_blocks for ref in block.refs
    )
    inserted = False
    final_messages: list[Message] = []
    for message_index, message in remaining_items:
        if not inserted and message_index >= first_compacted_message_index:
            final_messages.append(compaction_message)
            inserted = True
        final_messages.append(message)
    if not inserted:
        final_messages.append(compaction_message)
    return final_messages


def _messages_chars(messages: list[Message]) -> int:
    return sum(_part_chars(part) for message in messages for part in message.parts)


def _part_chars(part: MessagePart) -> int:
    if part.type is MessagePartType.TEXT:
        return len(part.text or "")
    if part.type is MessagePartType.REASONING:
        return len(part.reasoning or part.text or "")
    if part.type is MessagePartType.ERROR:
        return len(part.text or "")
    if part.type is MessagePartType.TOOL_CALL and part.tool_call is not None:
        return len(part.tool_call.tool_name) + len(part.tool_call.arguments_text or "")
    if part.type is MessagePartType.TOOL_RESULT and part.tool_result is not None:
        return len(part.tool_result.content or "")
    if part.type is MessagePartType.ATTACHMENT and part.attachment is not None:
        metadata = {
            "attachment_id": part.attachment.attachment_id,
            "mime_type": part.attachment.mime_type,
            "filename": part.attachment.filename,
            "url": part.attachment.url,
            "metadata": _copy_mapping(part.attachment.metadata),
            "created_at": part.attachment.created_at,
        }
        return len(part.attachment.text_ref or part.text or "") + _json_chars(metadata)
    if part.type is MessagePartType.TASK and part.task is not None:
        metadata = {
            "task_id": part.task.task_id,
            "description": part.task.description,
            "status": part.task.status,
            "agent": part.task.agent,
            "model": part.task.model,
            "metadata": _copy_mapping(part.task.metadata),
        }
        return len(part.task.prompt or part.text or "") + _json_chars(metadata)
    if part.type is MessagePartType.COMPACTION and part.compaction is not None:
        metadata = {
            "source_message_ids": list(part.compaction.source_message_ids),
            "auto": part.compaction.auto,
            "overflow": part.compaction.overflow,
            "tail_start_message_id": part.compaction.tail_start_message_id,
            "original_part_count": part.compaction.original_part_count,
            "original_message_count": part.compaction.original_message_count,
            "tool_pair_count": part.compaction.tool_pair_count,
            "metadata": _copy_mapping(part.compaction.metadata),
        }
        return len(part.compaction.summary or part.text or "") + _json_chars(metadata)
    return len(part.text or "")


def _compaction_summary_chars(compacted_blocks: list[_Block]) -> int:
    if not compacted_blocks:
        return 0
    return _messages_chars([_build_compaction_message(compacted_blocks)])


def _json_chars(value: Mapping[str, Any]) -> int:
    return len(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")))


def _copy_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return dict(mapping)


def _validate_non_negative_int(value: Any, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
