from __future__ import annotations


def _artifact_ids(bundle: dict) -> list[str]:
    artifact_refs = bundle.get("artifact_refs") or []
    return [str(ref.get("artifact_id")) for ref in artifact_refs if isinstance(ref, dict) and ref.get("artifact_id")]


def _add_preview(lines: list[str], bundle: dict, *, include_preview: bool) -> None:
    if not include_preview:
        return
    preview = str(bundle.get("content_markdown") or bundle.get("body_markdown") or "").strip()
    partial_reasons = [str(r) for r in ((bundle.get("completeness_ledger") or {}).get("partial_reasons") or []) if r]
    if preview:
        lines.extend(["", "[preview]", preview[:1000]])
    elif "non_projectable_file" in partial_reasons:
        lines.append("projection: materialized_as_artifact_only (unsupported text projection)")
    elif any(str(r).startswith("parse_failed:") for r in partial_reasons):
        lines.append("projection: parse_failed_after_materialization (text unavailable)")


def format_github_source_manifest(bundle: dict, *, include_preview: bool = True) -> str:
    metadata = bundle.get("metadata") or {}
    completeness = bundle.get("completeness_ledger") or {}
    source_kind = metadata.get("source_kind") or "repo_file"
    artifact_ids = _artifact_ids(bundle)

    lines = [
        "[github source bundle prepared]",
        f"source_kind: {source_kind}",
    ]

    if source_kind == "repo_file":
        owner = metadata.get("owner") or "unknown"
        repo = metadata.get("repo") or "unknown"
        path = metadata.get("path") or ""
        branch = metadata.get("branch") or "unknown"
        lines.insert(1, f"file: {owner}/{repo}:{path}@{branch}")
    elif source_kind == "issue":
        lines.append(f"issue: {(metadata.get('repo_full_name') or 'unknown')}#{metadata.get('issue_number')}")
    elif source_kind == "pull_request":
        lines.append(f"pull_request: {(metadata.get('repo_full_name') or 'unknown')}#{metadata.get('pull_number')}")

    lines.extend(
        [
            f"artifact_refs: {artifact_ids}",
            f"context_ref: {bundle.get('context_ref')}",
            f"digest_ref: {bundle.get('digest_ref')}",
            f"source_complete: {bool(completeness.get('source_complete', False))}",
        ]
    )

    _add_preview(lines, bundle, include_preview=include_preview)

    partial_reasons = [str(r) for r in (completeness.get("partial_reasons") or []) if r]
    if partial_reasons:
        lines.append(f"partial_reasons: {partial_reasons}")

    return "\n".join(lines)
