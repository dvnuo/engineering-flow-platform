from __future__ import annotations


def format_github_source_manifest(bundle: dict, *, include_preview: bool = True) -> str:
    metadata = bundle.get("metadata") or {}
    completeness = bundle.get("completeness_ledger") or {}
    owner = metadata.get("owner") or "unknown"
    repo = metadata.get("repo") or "unknown"
    path = metadata.get("path") or ""
    branch = metadata.get("branch") or "unknown"
    source_kind = metadata.get("source_kind") or "repo_file"
    artifact_refs = bundle.get("artifact_refs") or []
    artifact_ids = [str(ref.get("artifact_id")) for ref in artifact_refs if isinstance(ref, dict) and ref.get("artifact_id")]
    lines = [
        "[github source bundle prepared]",
        f"file: {owner}/{repo}:{path}@{branch}",
        f"source_kind: {source_kind}",
        f"artifact_refs: {artifact_ids}",
        f"context_ref: {bundle.get('context_ref')}",
        f"digest_ref: {bundle.get('digest_ref')}",
        f"source_complete: {bool(completeness.get('source_complete', False))}",
    ]

    partial_reasons = [str(r) for r in (completeness.get("partial_reasons") or []) if r]
    preview = str(bundle.get("content_markdown") or "").strip()
    if include_preview:
        if preview:
            lines.extend(["", "[preview]", preview[:1000]])
        elif "non_projectable_file" in partial_reasons:
            lines.append("projection: materialized_as_artifact_only (unsupported text projection)")
        elif any(str(r).startswith("parse_failed:") for r in partial_reasons):
            lines.append("projection: parse_failed_after_materialization (text unavailable)")

    if partial_reasons:
        lines.append(f"partial_reasons: {partial_reasons}")

    return "\n".join(lines)
