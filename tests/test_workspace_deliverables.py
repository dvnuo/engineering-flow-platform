"""Deliverable detection: files written under output/ become file display blocks."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.gateway import workspace_deliverables as deliverables


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(deliverables.DELIVERABLES_DIR_ENV, raising=False)
    monkeypatch.delenv(deliverables.MAX_FILES_ENV, raising=False)


def _touch(path: Path, content: bytes = b"x", *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_deliverables_dir_defaults_to_output_and_rejects_escapes(monkeypatch):
    assert deliverables.deliverables_dir_name() == "output"

    monkeypatch.setenv(deliverables.DELIVERABLES_DIR_ENV, "deliverables/decks/")
    assert deliverables.deliverables_dir_name() == "deliverables/decks"

    for bad in ("../elsewhere", "/abs", "a/../b", "   "):
        monkeypatch.setenv(deliverables.DELIVERABLES_DIR_ENV, bad)
        assert deliverables.deliverables_dir_name() == "output", bad


def test_snapshot_lists_regular_files_recursively_and_skips_hidden(tmp_path: Path):
    assert deliverables.snapshot_deliverables(tmp_path) == {}
    assert deliverables.snapshot_deliverables(None) == {}

    _touch(tmp_path / "output" / "deck.pptx", b"deck")
    _touch(tmp_path / "output" / "charts" / "q3.png", b"png")
    _touch(tmp_path / "output" / ".hidden.json", b"{}")
    _touch(tmp_path / "output" / ".cache" / "tmp.bin", b"tmp")
    _touch(tmp_path / "elsewhere.txt", b"not watched")

    snapshot = deliverables.snapshot_deliverables(tmp_path)

    assert set(snapshot) == {"output/deck.pptx", "output/charts/q3.png"}
    assert snapshot["output/deck.pptx"][1] == 4


def test_diff_reports_created_and_updated_newest_first(tmp_path: Path):
    old = _touch(tmp_path / "output" / "old.md", b"old", mtime=1_000)
    kept = _touch(tmp_path / "output" / "kept.md", b"kept", mtime=1_000)
    before = deliverables.snapshot_deliverables(tmp_path)

    _touch(old, b"old-but-longer", mtime=2_000)
    _touch(tmp_path / "output" / "new.pptx", b"new", mtime=3_000)
    _touch(kept, b"kept", mtime=1_000)
    after = deliverables.snapshot_deliverables(tmp_path)

    assert deliverables.diff_deliverables(before, after) == [
        ("output/new.pptx", "created"),
        ("output/old.md", "updated"),
    ]


def test_diff_caps_the_number_of_reported_files(monkeypatch):
    monkeypatch.setenv(deliverables.MAX_FILES_ENV, "2")
    after = {f"output/{i}.txt": (i, 1) for i in range(5)}

    reported = deliverables.diff_deliverables({}, after)

    assert reported == [("output/4.txt", "created"), ("output/3.txt", "created")]


def test_file_block_carries_name_size_type_and_action(tmp_path: Path):
    _touch(tmp_path / "output" / "Q3 review.pptx", b"12345")

    block = deliverables.build_file_display_block(tmp_path, "output/Q3 review.pptx", action="updated")

    assert block["type"] == "file"
    assert block["path"] == "output/Q3 review.pptx"
    assert block["name"] == "Q3 review.pptx"
    assert block["size"] == 5
    assert block["action"] == "updated"
    assert block["content_type"].endswith("presentationml.presentation")
    assert block["modified_at"].endswith("+00:00")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("notes.md", "text/markdown"),
        ("data.csv", "text/csv"),
        ("mystery.zzz", "application/octet-stream"),
    ],
)
def test_guess_content_type(name, expected):
    assert deliverables.guess_content_type(name) == expected


def test_build_blocks_end_to_end(tmp_path: Path):
    before = deliverables.snapshot_deliverables(tmp_path)
    _touch(tmp_path / "output" / "deck.pptx", b"deck")

    blocks = deliverables.build_deliverable_display_blocks(tmp_path, before)

    assert [(b["type"], b["path"], b["action"]) for b in blocks] == [("file", "output/deck.pptx", "created")]
    assert deliverables.build_deliverable_display_blocks(None, before) == []


def test_attach_keeps_reply_text_as_the_first_block():
    payload = {"response": "Here is the deck.", "content": "Here is the deck."}
    file_block = {"type": "file", "path": "output/deck.pptx", "name": "deck.pptx"}

    deliverables.attach_deliverable_blocks(payload, [file_block])

    assert payload["display_blocks"] == [
        {"type": "markdown", "content": "Here is the deck."},
        file_block,
    ]


def test_attach_appends_to_existing_blocks_and_ignores_empty():
    payload = {"response": "x", "display_blocks": [{"type": "code", "content": "print(1)"}]}
    deliverables.attach_deliverable_blocks(payload, [])
    assert "display_blocks" in payload and len(payload["display_blocks"]) == 1

    deliverables.attach_deliverable_blocks(payload, [{"type": "file", "path": "output/a.txt", "name": "a.txt"}])
    assert [b["type"] for b in payload["display_blocks"]] == ["code", "file"]


def test_attach_without_reply_text_emits_only_file_blocks():
    payload = {"response": "   ", "content": ""}
    deliverables.attach_deliverable_blocks(payload, [{"type": "file", "path": "output/a.txt", "name": "a.txt"}])
    assert payload["display_blocks"] == [{"type": "file", "path": "output/a.txt", "name": "a.txt"}]


class _FakeStore:
    def __init__(self, messages):
        self.messages = messages
        self.replaced = None

    def read_history(self, _session_id):
        return list(self.messages)

    def replace_history(self, _session_id, messages):
        self.replaced = list(messages)


def test_persist_records_blocks_on_the_matching_assistant_message():
    other = SimpleNamespace(message_id="msg-1", metadata={})
    target = SimpleNamespace(message_id="msg-2", metadata={"keep": True})
    store = _FakeStore([other, target])
    blocks = [{"type": "file", "path": "output/deck.pptx", "name": "deck.pptx"}]

    assert deliverables.persist_deliverable_blocks(store, "s1", "msg-2", blocks) is True

    assert store.replaced is not None
    assert target.metadata == {"keep": True, deliverables.DELIVERABLE_BLOCKS_METADATA_KEY: blocks}
    assert other.metadata == {}


def test_persist_is_a_no_op_without_a_message_or_blocks_and_survives_store_errors():
    store = _FakeStore([SimpleNamespace(message_id="msg-1", metadata={})])
    blocks = [{"type": "file", "path": "output/a.txt", "name": "a.txt"}]

    assert deliverables.persist_deliverable_blocks(store, "s1", None, blocks) is False
    assert deliverables.persist_deliverable_blocks(store, "s1", "msg-1", []) is False
    assert deliverables.persist_deliverable_blocks(store, "s1", "missing", blocks) is False
    assert store.replaced is None

    class _BrokenStore:
        def read_history(self, _session_id):
            raise OSError("disk gone")

    assert deliverables.persist_deliverable_blocks(_BrokenStore(), "s1", "msg-1", blocks) is False
