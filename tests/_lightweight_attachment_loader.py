from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from tests._lightweight_file_parser_loader import load_file_parser_lightweight


class _Storage:
    def __init__(self):
        self.records: dict[str, object] = {}

    def get_artifact(self, artifact_id: str):
        return self.records.get(str(artifact_id))

    def update_artifact_status(self, artifact_id, *, parse_status=None, parse_error=None):
        rec = self.records.get(str(artifact_id))
        if rec is None:
            rec = types.SimpleNamespace(artifact_id=str(artifact_id), projection_kind=None, preview=None, text_ref=None)
            self.records[str(artifact_id)] = rec
        if parse_status is not None:
            rec.parse_status = parse_status
        if parse_error is not None:
            rec.parse_error = parse_error
        return rec


def load_attachment_lightweight():
    file_parser_module, file_parser_cleanup = load_file_parser_lightweight()

    src_pkg = types.ModuleType("src")
    src_pkg.__path__ = []

    utils_pkg = types.ModuleType("src.utils")
    utils_pkg.__path__ = []

    file_artifacts_pkg = types.ModuleType("src.file_artifacts")
    file_artifacts_pkg.__path__ = []
    file_artifacts_pkg.can_project_to_text = (
        lambda mime, filename: str(mime or "").startswith("text/")
        or str(mime or "")
        in {
            "application/pdf",
            "application/json",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    )

    storage = _Storage()

    service_mod = types.ModuleType("src.file_artifacts.service")

    def _register_existing_file_as_artifact(file_id, **kwargs):
        rec = types.SimpleNamespace(
            artifact_id=str(file_id),
            file_id=str(file_id),
            projection_kind=None,
            preview=None,
            text_ref=None,
            parse_status="pending",
            parse_error=None,
        )
        storage.records[str(file_id)] = rec
        return rec

    def _update_projection_from_parse_result(artifact_id, parsed, preview=None, **kwargs):
        rec = storage.records.get(str(artifact_id))
        if rec is None:
            rec = types.SimpleNamespace(artifact_id=str(artifact_id))
            storage.records[str(artifact_id)] = rec
        rec.projection_kind = "text"
        rec.preview = preview if preview is not None else (getattr(parsed, "markdown", "") or "")[:2000]
        rec.text_ref = f"ctx://context/{kwargs.get('persist_text_ref_session_id','s')}/{kwargs.get('persist_text_ref_kind','k')}/sha"
        rec.parse_status = "completed"
        rec.parse_error = None
        return rec

    service_mod.register_existing_file_as_artifact = _register_existing_file_as_artifact
    service_mod.update_projection_from_parse_result = _update_projection_from_parse_result

    storage_mod = types.ModuleType("src.file_artifacts.storage")
    storage_mod.storage = storage

    modules = {
        "src": src_pkg,
        "src.utils": utils_pkg,
        "src.utils.file_parser": file_parser_module,
        "src.file_artifacts": file_artifacts_pkg,
        "src.file_artifacts.service": service_mod,
        "src.file_artifacts.storage": storage_mod,
    }
    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.utils = utils_pkg

    spec = importlib.util.spec_from_file_location("src.utils.attachment", Path("src/utils/attachment.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.utils.attachment"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.utils.attachment", None)
        file_parser_cleanup()

    return module, _cleanup
