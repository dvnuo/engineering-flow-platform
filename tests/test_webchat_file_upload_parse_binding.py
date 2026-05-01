import io
import json

import pytest
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

try:  # pragma: no cover - environment dependent
    import ruamel.yaml as _ruamel_yaml  # noqa: F401
    _HAS_RUAMEL_YAML = True
except Exception:  # pragma: no cover - environment dependent
    _HAS_RUAMEL_YAML = False

if not _HAS_RUAMEL_YAML:  # pragma: no cover - environment dependent
    pytest.skip("full webchat runtime dependencies unavailable (missing ruamel.yaml)", allow_module_level=True)

from src.gateway import webchat
from src.hooks.file_context.storage import storage

class _Multipart:
    def __init__(self, field):
        self._field = field
    async def next(self):
        return self._field


class _Field:
    name = "file"
    filename = "a.txt"
    async def read(self, *args, **kwargs):
        return b"hello world"


@pytest.mark.asyncio
async def test_upload_and_parse_binds_session_and_rebuilds(monkeypatch):
    req = make_mocked_request("POST", "/api/files/upload?session_id=s-bind", headers=CIMultiDict())
    async def _multipart():
        return _Multipart(_Field())
    req.multipart = _multipart

    resp = await webchat.api_files_upload(req)
    payload = json.loads(resp.text)
    fid = payload["file_id"]

    meta = storage.get_file_meta("s-bind", fid)
    assert meta is None

    called = {"n": 0}
    monkeypatch.setattr("src.hooks.file_context.retrieval.retrieval_engine.rebuild_index", lambda sid: called.__setitem__("n", called["n"] + 1))

    parse_req = make_mocked_request("POST", "/api/files/parse?session_id=s-bind", headers=CIMultiDict())
    async def _json():
        return {"file_id": fid}
    parse_req.json = _json

    parse_resp = await webchat.api_files_parse(parse_req)
    parse_payload = json.loads(parse_resp.text)
    assert parse_payload["success"] is True
    meta2 = storage.get_file_meta("s-bind", fid)
    assert meta2.parse_status == "completed"
    assert storage.get_file_chunks(fid)
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_parse_exception_marks_file_failed(monkeypatch):
    req = make_mocked_request("POST", "/api/files/upload?session_id=s-bind-err", headers=CIMultiDict())

    async def _multipart():
        return _Multipart(_Field())

    req.multipart = _multipart
    upload_resp = await webchat.api_files_upload(req)
    file_id = json.loads(upload_resp.text)["file_id"]

    async def _raise_parse(file_id, options=None):
        raise RuntimeError("parse crash")

    monkeypatch.setattr(webchat, "parse_file", _raise_parse)

    parse_req = make_mocked_request("POST", "/api/files/parse?session_id=s-bind-err", headers=CIMultiDict())

    async def _json():
        return {"file_id": file_id}

    parse_req.json = _json
    parse_resp = await webchat.api_files_parse(parse_req)
    assert parse_resp.status == 500

    meta = storage.get_file_meta("s-bind-err", file_id)
    assert meta is not None
    assert meta.parse_status == "failed"


@pytest.mark.asyncio
async def test_delete_file_best_effort_cleans_context_even_when_storage_missing(monkeypatch):
    session_id = "s-delete-bind"
    file_id = "fid-delete-bind"

    class _DummyContextStorage:
        def __init__(self):
            self.calls = []

        def remove_file_from_session(self, sid, fid):
            self.calls.append((sid, fid))
            return True

    class _DummyRetrieval:
        def __init__(self):
            self.calls = []

        def rebuild_index(self, sid):
            self.calls.append(sid)

    dummy_context_storage = _DummyContextStorage()
    dummy_retrieval = _DummyRetrieval()

    monkeypatch.setattr("src.hooks.file_context.storage.storage", dummy_context_storage)
    monkeypatch.setattr("src.hooks.file_context.retrieval.retrieval_engine", dummy_retrieval)
    monkeypatch.setattr("src.utils.file_parser.storage.delete_file", lambda _fid: False)

    req = make_mocked_request(
        "DELETE",
        f"/api/files/{file_id}?session_id={session_id}",
        headers=CIMultiDict(),
        match_info={"file_id": file_id},
    )
    resp = await webchat.api_files_delete(req)
    payload = json.loads(resp.text)

    assert resp.status == 200
    assert payload == {"success": True}
    assert dummy_context_storage.calls == [(session_id, file_id)]
    assert dummy_retrieval.calls == [session_id]
