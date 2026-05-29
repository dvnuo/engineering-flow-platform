# EFP Native Runtime Contract

## Scope

This document defines the native Engineering Flow Platform (EFP) runtime contract exposed to Portal and integration smoke suites.

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

- External skills directory resolves from `EFP_SKILLS_DIR` first, then `/app/skills`.
- Default workspace directory is `~/.efp/workspace`.
- Docker image provisioning creates `/app/skills`, `/root/.efp/workspace`, and `/root/.efp/skills`.

## Tool Surface

- EFP native runtime uses Runtime v2 (`efp_runtime.runtime.AgentRuntime`) for `/api/chat`, `/api/chat/stream`, and Jira chat handling.
- Runtime v2 native mode supports GitHub Copilot only. Configure `llm.provider: github_copilot` plus `llm.api_key` or `EFP_GITHUB_COPILOT_TOKEN`.
- Runtime tool surface comes from the opencode-style Runtime v2 built-in registry only (`src.__init__.get_tools_schema()`).
- Model-visible tool ids include `bash`, `read`, `write`, `edit`, `grep`, `glob`, `webfetch`, `todowrite`, and `apply_patch`.
- Legacy Python tool packages such as `src.bash_tools` are not present, and Jira/GitHub/Confluence/Git Python tools are not exposed as LLM tools.
- Legacy external-tools envs (`EFP_TOOLS_DIR`, `EFP_EXTERNAL_TOOLS_*`) are ignored by native runtime.

## External Skills Surface

- Business skills are not stored in this repository and come from `engineering-flow-platform-skills`.
- Portal is responsible for skills repo/branch provisioning only.
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

`tests/fixtures/runtime_contract` is the deterministic fixture used by native runtime contract tests.
