# Control Plane Trust Contract (Phase 5 Closeout)

This document defines the Portal ↔ EFP runtime trust boundaries.

## 1) Chat Entrypoints (`/api/chat`, `/api/chat/stream`)

- Direct runtime chat is allowed without Portal (from inside the pod only, once the pod has a trust token; see section 2).
- Trusted Portal metadata/identity is only accepted when request is trusted:
  - `X-Portal-Author-Source: portal`
  - and, when the pod has `EFP_PORTAL_TRUST_TOKEN`, the same value in `X-Portal-Trust-Token` (compared in constant time)
- Portal identity is header-only:
  - `X-Portal-User-Id`
  - `X-Portal-User-Name`
- Trusted Portal chat metadata may also carry `portal_user` (`{id, username, display_name}`), the structured
  form of the identity headers. EFP renders it (or, failing that, the header values) into a `Session user:`
  system message with identity rules, so the model treats `jira myself` / `currentUser()` as the shared service
  account and filters "my" requests by the session user explicitly. Untrusted requests cannot set it.
- `portal_user` may also carry `github_login`, the member's GitHub login (the Portal username with the enterprise
  suffix put back). EFP lists it in the `Session user:` message so "my pull requests" is filtered by that login
  instead of `@me`, which is the service account; a value that does not look like a login is dropped.
- The same `portal_user` object may carry `reply_language` (`en`, `zh-Hans`, `zh-Hant-HK`) and
  `reply_language_strict` (boolean; `true` when the member chose the language explicitly), the Portal's resolved
  reply language for the member. EFP renders a `Reply language:` system message directly after `Session user:`
  (omitted when the field is absent or unknown, and for English unless chosen explicitly) and names sessions in
  the language of the assistant's answer. The Portal sends it on chat, question/permission answers, message edits
  and `title/generate`; untrusted requests cannot set it.
- Untrusted requests cannot inject governance metadata.

## 2) Runtime Internal Endpoints

- `/api/tasks/execute` and `/api/capabilities` are internal control-plane endpoints.
- Runtime profile apply still relies on trusted Portal source marker and deployment topology.
- Portal trust token: the Portal injects `EFP_PORTAL_TRUST_TOKEN` (a per-assistant secret) into the pod and sends it
  as `X-Portal-Trust-Token` on every request, the `GET /api/events` WebSocket handshake included. While the variable
  is set, one middleware (`src/gateway/portal_trust.py`) answers `401 {"error": "portal_trust_token_required"}` to
  every request without the right token, on every route. Served without it:
  - `GET /health`, `GET /actuator/health`, `GET /ready` (the kubelet's readiness probe and the Portal's status
    check cannot hold the token);
  - callers on the pod's own loopback interface (socket peer `127.0.0.1` or `::1`; a request carrying
    `Forwarded`, `X-Forwarded-For` or `X-Real-IP` never counts as one).

  `POST /webhook/jira` is not among them: on a pod with a token it answers `401` to Jira itself, which cannot send
  the header. The route starts a chat run and writes a Jira comment, so it stays closed; Jira events reach an
  assistant through the Portal (which polls the sources), not through a webhook that points at the pod.
- A pod without the variable (created before the Portal injected it, or local development) serves every caller as
  before and logs one warning at startup.

## 3) Runtime Adapter → Portal Internal API Contract

- `adapter:portal:*` actions call Portal internal APIs with:
  - base URL from `PORTAL_INTERNAL_BASE_URL` env, fallback `server.portal_internal_base_url`
  - `X-Portal-Trust-Token` when the pod has `EFP_PORTAL_TRUST_TOKEN` (the session metadata publishes and the Jira
    reconciliation send it too)

## 4) Trust Matrix

| Chain | EFP setting | Portal side should provide |
|---|---|---|
| Portal → EFP, every route | `EFP_PORTAL_TRUST_TOKEN` (unset: permissive) | `X-Portal-Trust-Token` |
| Portal → EFP `/api/tasks/execute`, `/api/capabilities` | none beyond the trust token | none beyond the trust token |
| Portal → EFP trusted chat metadata/identity | `X-Portal-Author-Source: portal` (plus the trust token) | `X-Portal-User-Id`, `X-Portal-User-Name` (optional) |
| Runtime adapter (`adapter:portal:*`) → Portal internal API | `server.portal_internal_base_url` / `PORTAL_INTERNAL_BASE_URL`, `EFP_PORTAL_TRUST_TOKEN` | internal route available at configured base URL |

## 5) Jira Reconciliation Contract (Runtime Fallback)

- Runtime reconciliation must ingest into Portal using the **ExternalEventIngressRequest** shape.
- Required reconciliation event shape:
  - `source_type = "jira"`
  - `event_type = "workflow_review_requested"`
  - `target_ref = <project_key>`
  - `payload_json` (JSON string)
  - Jira contextual fields (`project_key`, `issue_type`, `trigger_status`, `issue_key`, `issue_assignee`)
  - optional `dedupe_key`, `metadata_json`, `external_account_id`
- Do **not** send legacy nested event envelopes (`event_key` / `source_ref` / `occurred_at` / nested `payload`) to `/api/internal/external-events/ingest`.

## 6) Session Metadata Compatibility

- Runtime session metadata publish keeps canonical field precedence:
  - `group_id`, `current_task_id`, `current_delegation_id`, `current_coordination_run_id`, `source_type`, `source_ref`.
- Runtime publish also supports compatibility aliases from Portal dispatch metadata (`portal_*` fields), while preserving original metadata in `metadata_json`.

## 7) GitHub Review Task Freshness Guard

- Portal may dispatch `github_review_task` with `head_sha`.
- Runtime treats `head_sha` as a write-back freshness guard before secondary GitHub review/comment submission.
- If current PR head SHA no longer matches the dispatched `head_sha`, runtime marks task as superseded (`error_code = superseded_by_new_head_sha`, `stale = true`) and skips write-back.
