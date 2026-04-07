# Control Plane Trust Contract (Phase 5 Closeout)

This document defines the Portal ↔ EFP runtime trust boundaries.

## 1) Chat Entrypoints (`/api/chat`, `/api/chat/stream`)

- Direct runtime chat is allowed without Portal.
- Trusted Portal metadata/identity is only accepted when request is trusted:
  - `X-Portal-Author-Source: portal`
  - and if configured (`PORTAL_INTERNAL_API_KEY` or `server.portal_internal_api_key`):
    `X-Portal-Internal-Api-Key`
- Portal identity is header-only:
  - `X-Portal-User-Id`
  - `X-Portal-User-Name`
- Untrusted requests cannot inject governance metadata.

## 2) Runtime Internal Endpoints

- `/api/tasks/execute` and `/api/capabilities` are internal control-plane endpoints.
- They require `X-Internal-Api-Key`.
- Key source (runtime side):
  - `RUNTIME_INTERNAL_API_KEY` (env) first
  - fallback `server.runtime_internal_api_key` in config

## 3) Runtime Adapter → Portal Internal API Contract

- `adapter:portal:*` actions call Portal internal APIs with:
  - base URL from `PORTAL_INTERNAL_BASE_URL` env, fallback `server.portal_internal_base_url`
  - `X-Internal-Api-Key` (from `PORTAL_INTERNAL_API_KEY` env, fallback `server.portal_internal_api_key`)
  - optional `Authorization: Bearer <PORTAL_INTERNAL_AUTH_TOKEN>` (legacy compatibility; env first, fallback `server.portal_internal_auth_token`)
- This is Runtime → Portal API auth and is distinct from chat trust header `X-Portal-Internal-Api-Key`.

## 4) Key Pairing Matrix

| Chain | EFP setting | Portal side should provide |
|---|---|---|
| Portal → EFP `/api/tasks/execute`, `/api/capabilities` | `server.runtime_internal_api_key` or `RUNTIME_INTERNAL_API_KEY` | `X-Internal-Api-Key` |
| Portal → EFP trusted chat metadata/identity | `server.portal_internal_api_key` or `PORTAL_INTERNAL_API_KEY` | `X-Portal-Internal-Api-Key` |
| Runtime adapter (`adapter:portal:*`) → Portal internal API | `server.portal_internal_base_url` / `PORTAL_INTERNAL_BASE_URL`; `server.portal_internal_api_key` / `PORTAL_INTERNAL_API_KEY`; optional `server.portal_internal_auth_token` / `PORTAL_INTERNAL_AUTH_TOKEN` | `X-Internal-Api-Key` (+ optional `Authorization`) |
