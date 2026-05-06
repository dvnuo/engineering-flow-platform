# EFP Native Runtime Contract

## Scope

This document defines the native Engineering Flow Platform (EFP) runtime contract exposed to Portal and integration smoke suites.
Opencode runtime is a separate runtime implementation, but Portal's unified runtime ingress remains the runtime service on `:8000`.

## Required HTTP Surface

Native runtime must support:

- `GET /health`
- `GET /actuator/health`
- `POST /api/chat`
- `POST /api/chat/stream`
- `GET /api/events`
- `GET /api/capabilities`
- `GET /api/skills`
- `POST /api/tasks/execute`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/internal/runtime-profile/apply`
- `GET /api/usage`
- `GET /api/sessions`

## Runtime Asset Directories

- External tools directory resolves from `EFP_TOOLS_DIR` first, then `/app/tools`.
- External skills directory resolves from `EFP_SKILLS_DIR` first, then `/app/skills`.
- Default workspace directory is `~/.efp/workspace`.
- Docker image provisioning creates `/app/skills`, `/app/tools`, `/root/.efp/workspace`, and `/root/.efp/skills`.

## External Tools Surface

- Primary external tool implementation lives in `src.tools_external.*`.
- `src/runtime/external_tools.py` remains a compatibility wrapper surface.
- `src.__init__.get_tools_schema()` merges legacy tools with external tools.
- For same-name collisions, external tool replaces a legacy tool only when the external descriptor sets `metadata.allow_override=true`.
- Strict mode environment switch is `EFP_EXTERNAL_TOOLS_STRICT=true`.
- Capability metadata fields include `tool_source`, `schema_source`, `execution_source`, `external_shadowed_by_legacy`, and `external_shadow_reason`.

## External Skills Surface

- Business skills are not stored in this repository and come from `engineering-flow-platform-skills`.
- Native runtime loads skills from `EFP_SKILLS_DIR` or `/app/skills`.
- Canonical skill file path is `<skill>/skill.md`.

## Capability Snapshot

`/api/capabilities` returns:

- `capabilities`
- `count`
- `catalog_version`
- `generated_at`
- `supports_snapshot_contract`
- `runtime_contract_version`

Each capability item includes at least:

- `capability_id`
- `type`
- `name`
- `input_schema`
- `output_schema`
- `policy_tags`
- `requires_identity_binding`
- `enabled`
- `source_ref`
- `metadata`

## Test Fixture

`tests/fixtures/runtime_contract` is the deterministic fixture used by native runtime contract tests and as the EFP baseline for future cross-repo smoke validation.
