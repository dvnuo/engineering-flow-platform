# EFP Native Runtime Contract

## Scope

This document defines the native Engineering Flow Platform (EFP) runtime contract exposed to Portal and integration smoke suites.

The native runtime is an API-only service. It does not serve a built-in browser page, root HTML route, bundled asset routes, template files, or frontend assets; Portal is the UI.

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
- Default workspace directory is `/workspace`.
- Docker image provisioning creates `/app/skills` and `/workspace`.

## Tool Surface

- EFP native runtime uses EFP runtime (`efp_runtime.runtime.AgentRuntime`) for `/api/chat`, `/api/chat/stream`, and Jira chat handling.
- EFP runtime native mode supports GitHub Copilot only. Configure `llm.provider: github_copilot` plus `llm.api_key` or `EFP_GITHUB_COPILOT_TOKEN`.
- Runtime tool surface comes from the EFP-owned runtime built-in registry only (`src.__init__.get_tools_schema()`).
- Model-visible tool ids include `bash`, `read`, `write`, `edit`, `grep`, `glob`, `webfetch`, `todowrite`, and `apply_patch`.
- Legacy Python tool packages such as `src.bash_tools` are not present, and Jira/GitHub/Confluence/Git Python tools are not exposed as LLM tools.
- The runtime image may include prebuilt `engineering-flow-platform-tools` CLI binaries on `PATH` in `/usr/local/bin`. Current binaries include `jira`, `confluence`, `browser`, and `mobile-auto`; future binaries are discovered from `cmd/<tool>` in that repo.
- Agents use those CLIs through the model-visible `bash` built-in in the workspace-full-access runtime workspace. They should run `<tool> commands --json`, then `<tool> schema <command> --json`, prefer `--json`, use `--dry-run` before writes, and pass `--yes` for destructive operations.
- Runtime profile application still projects Jira, Confluence, GitHub, Git, and mobile BrowserStack configuration to the corresponding CLI config files.
- Private managed mobile runs require BrowserStackLocal at `/usr/local/bin/BrowserStackLocal` or a configured `BROWSERSTACK_LOCAL_BINARY`; CI may stage that third-party binary into `runtime-tools/BrowserStackLocal`.
- Legacy `EFP_TOOLS_DIR` / `EFP_EXTERNAL_TOOLS_*` Python external tool loaders are ignored by native runtime. `runtime-tools/*` is a Docker/CI build input for prebuilt CLI binaries and is copied into `PATH`; it is not a Python loader.
- MCP servers and external protocol tool providers are intentionally excluded.

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
