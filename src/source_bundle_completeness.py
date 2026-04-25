from __future__ import annotations


def apply_session_scope_requirement(
    ledger: dict,
    *,
    has_context_ref: bool,
    has_digest_ref: bool,
) -> dict:
    if has_context_ref and has_digest_ref:
        return ledger

    partial_reasons = ledger.setdefault("partial_reasons", [])
    if "session_scope_missing" not in partial_reasons:
        partial_reasons.append("session_scope_missing")

    ledger["source_complete_for_generation"] = False
    ledger["source_complete_including_binary_bodies"] = False
    ledger["source_complete"] = False
    return ledger

