"""Session-scoped durable context blob store for model-facing projection."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path.home() / ".efp" / "workspace" / "context_blobs"


def _safe_segment(value: str, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    text = text.strip("._-")
    return text or fallback


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _blob_path(session_id: str, kind: str, sha256: str) -> Path:
    return _ROOT / _safe_segment(session_id) / _safe_segment(kind) / f"{sha256}.json"


def _parse_ref(ref: str) -> Tuple[str, str, str]:
    # ctx://context/{session}/{kind}/{sha12}
    m = re.match(r"^ctx://context/([^/]+)/([^/]+)/([a-f0-9]{12})$", str(ref or "").strip())
    if not m:
        raise ValueError(f"Invalid context ref: {ref}")
    return m.group(1), m.group(2), m.group(3)


def build_section_map(text: str) -> List[Dict[str, Any]]:
    content = str(text or "")
    lines = content.splitlines(keepends=True)
    offsets: List[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line)
    headings: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#"):
            match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
            if not match:
                continue
            headings.append(
                {
                    "heading": match.group(2).strip(),
                    "level": len(match.group(1)),
                    "start": offsets[idx],
                    "line": idx + 1,
                }
            )
    for i, item in enumerate(headings):
        item["end"] = headings[i + 1]["start"] if i + 1 < len(headings) else len(content)
    return headings


def put_text(
    session_id: str,
    kind: str,
    source_id: str,
    title: str,
    content: Any,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    full_text = str(content or "")
    sha256 = _sha256_text(full_text)
    session_safe = _safe_segment(session_id)
    kind_safe = _safe_segment(kind)
    source_safe = _safe_segment(source_id)
    path = _blob_path(session_safe, kind_safe, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_safe,
        "kind": kind_safe,
        "source_id": source_safe,
        "title": str(title or ""),
        "sha256": sha256,
        "sha12": sha256[:12],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "content": full_text,
    }
    if not path.exists():
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"ctx://context/{session_safe}/{kind_safe}/{sha256[:12]}"


def _load_blob_for_ref(ref: str) -> Dict[str, Any]:
    session_safe, kind_safe, sha12 = _parse_ref(ref)
    base = _ROOT / session_safe / kind_safe
    if not base.exists():
        raise ValueError("Context ref not found")
    for candidate in base.glob("*.json"):
        if candidate.stem.startswith(sha12):
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise ValueError("Context ref not found")


def _format_toc(section_map: List[Dict[str, Any]]) -> str:
    if not section_map:
        return "(no headings found)"
    lines = []
    for item in section_map:
        indent = "  " * max(0, int(item.get("level", 1)) - 1)
        lines.append(f"{indent}- {item.get('heading')} [chars {item.get('start')}..{item.get('end')}]")
    return "\n".join(lines)


def read_ref(
    ref: str,
    session_id: Optional[str] = None,
    section: Optional[str] = None,
    start: Optional[int] = None,
    max_chars: int = 6000,
) -> str:
    session_safe, _kind_safe, _sha12 = _parse_ref(ref)
    if session_id is not None and _safe_segment(session_id) != session_safe:
        raise PermissionError("Ref session mismatch")
    blob = _load_blob_for_ref(ref)
    text = str(blob.get("content") or "")
    selected = text
    section_name = str(section or "raw").strip().lower()
    if section_name == "toc":
        selected = _format_toc(build_section_map(text))
    elif section_name not in ("", "raw"):
        sec_map = build_section_map(text)
        normalized = re.sub(r"\s+", " ", section_name).strip()
        for item in sec_map:
            heading = str(item.get("heading") or "")
            heading_norm = re.sub(r"\s+", " ", heading.lower()).strip()
            if normalized in heading_norm or heading_norm in normalized:
                selected = text[int(item.get("start", 0)): int(item.get("end", len(text)))]
                break

    start_idx = max(0, int(start or 0))
    sliced = selected[start_idx:]
    cap = max(1, int(max_chars or 6000))
    if len(sliced) <= cap:
        return sliced
    return (
        f"{sliced[:cap]}\n\n[... output truncated at {cap} chars; "
        f"{len(sliced) - cap} chars omitted. Use start={start_idx + cap} to continue ...]"
    )

