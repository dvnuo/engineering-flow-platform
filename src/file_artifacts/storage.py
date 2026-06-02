from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.workspace_defaults import DEFAULT_RUNTIME_WORKSPACE

from .models import ArtifactBinding, ArtifactRecord


class FileArtifactStorage:
    def __init__(self, base_dir: str | Path = DEFAULT_RUNTIME_WORKSPACE / "file_artifacts"):
        self.base_dir = Path(base_dir).expanduser()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_path = self.base_dir / "artifacts.json"
        self.bindings_path = self.base_dir / "bindings.json"

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text())
        except Exception:
            return default

    def _write_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    def _load_artifacts(self) -> Dict[str, dict]:
        return self._read_json(self.artifacts_path, {})

    def _save_artifacts(self, payload: Dict[str, dict]) -> None:
        self._write_json(self.artifacts_path, payload)

    def _load_bindings(self) -> List[dict]:
        return self._read_json(self.bindings_path, [])

    def _save_bindings(self, payload: List[dict]) -> None:
        self._write_json(self.bindings_path, payload)

    def upsert_artifact(self, record: ArtifactRecord) -> ArtifactRecord:
        artifacts = self._load_artifacts()
        now = datetime.utcnow().isoformat() + "Z"
        payload = record.model_dump()
        existing = artifacts.get(record.artifact_id)
        if existing and existing.get("created_at"):
            payload["created_at"] = existing["created_at"]
        payload["updated_at"] = now
        artifacts[record.artifact_id] = payload
        self._save_artifacts(artifacts)
        return ArtifactRecord(**payload)

    def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        payload = self._load_artifacts().get(artifact_id)
        return ArtifactRecord(**payload) if payload else None

    def bind_artifact(self, binding: ArtifactBinding) -> ArtifactBinding:
        bindings = self._load_bindings()
        item = binding.model_dump()
        for existing in bindings:
            if existing.get("artifact_id") == binding.artifact_id and existing.get("scope_type") == binding.scope_type and existing.get("scope_id") == binding.scope_id and existing.get("role") == binding.role:
                return ArtifactBinding(**existing)
        bindings.append(item)
        self._save_bindings(bindings)
        return binding

    def list_scope_artifacts(self, scope_type: str, scope_id: str) -> List[ArtifactRecord]:
        bindings = self._load_bindings()
        artifact_ids = [
            b.get("artifact_id")
            for b in bindings
            if b.get("scope_type") == scope_type and b.get("scope_id") == scope_id
        ]
        artifacts = self._load_artifacts()
        return [ArtifactRecord(**artifacts[a]) for a in artifact_ids if a in artifacts]

    def update_artifact_projection(
        self,
        artifact_id: str,
        *,
        projection_kind: Optional[str],
        preview: Optional[str],
        chunk_count: int,
        total_chars: int,
    ) -> Optional[ArtifactRecord]:
        artifacts = self._load_artifacts()
        item = artifacts.get(artifact_id)
        if not item:
            return None
        item["projection_kind"] = projection_kind
        item["preview"] = preview
        item["chunk_count"] = int(chunk_count or 0)
        item["total_chars"] = int(total_chars or 0)
        item["updated_at"] = datetime.utcnow().isoformat() + "Z"
        artifacts[artifact_id] = item
        self._save_artifacts(artifacts)
        return ArtifactRecord(**item)

    def update_artifact_status(self, artifact_id: str, *, parse_status: str, parse_error: Optional[str] = None) -> Optional[ArtifactRecord]:
        artifacts = self._load_artifacts()
        item = artifacts.get(artifact_id)
        if not item:
            return None
        item["parse_status"] = parse_status
        item["parse_error"] = parse_error
        item["updated_at"] = datetime.utcnow().isoformat() + "Z"
        artifacts[artifact_id] = item
        self._save_artifacts(artifacts)
        return ArtifactRecord(**item)

    def update_artifact_references(
        self,
        artifact_id: str,
        *,
        text_ref: Optional[str] = None,
        context_ref: Optional[str] = None,
        digest_ref: Optional[str] = None,
        full_markdown_chars: Optional[int] = None,
    ) -> Optional[ArtifactRecord]:
        artifacts = self._load_artifacts()
        item = artifacts.get(artifact_id)
        if not item:
            return None
        if text_ref is not None:
            item["text_ref"] = text_ref
        if context_ref is not None:
            item["context_ref"] = context_ref
        if digest_ref is not None:
            item["digest_ref"] = digest_ref
        if full_markdown_chars is not None:
            item["full_markdown_chars"] = int(full_markdown_chars or 0)
        item["updated_at"] = datetime.utcnow().isoformat() + "Z"
        artifacts[artifact_id] = item
        self._save_artifacts(artifacts)
        return ArtifactRecord(**item)


storage = FileArtifactStorage()
