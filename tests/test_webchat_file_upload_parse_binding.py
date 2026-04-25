import io
import json

import pytest
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict

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
    try:
        from src.gateway import webchat
        from src.hooks.file_context.storage import storage
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"webchat import unavailable in this environment: {exc}")

    req = make_mocked_request("POST", "/api/files/upload?session_id=s-bind", headers=CIMultiDict())
    async def _multipart():
        return _Multipart(_Field())
    req.multipart = _multipart

    resp = await webchat.api_files_upload(req)
    payload = json.loads(resp.text)
    fid = payload["file_id"]

    meta = storage.get_file_meta("s-bind", fid)
    assert meta is not None
    assert meta.parse_status == "pending"

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
async def test_parse_exception_marks_file_and_artifact_failed(monkeypatch):
    try:
        from src.gateway import webchat
        from src.hooks.file_context.storage import storage
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"webchat import unavailable in this environment: {exc}")

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

    from src.file_artifacts.storage import storage as artifact_storage
    artifact = artifact_storage.get_artifact(file_id)
    assert artifact is not None
    assert artifact.parse_status == "failed"
