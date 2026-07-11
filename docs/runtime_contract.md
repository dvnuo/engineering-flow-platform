# EFP Native Runtime Contract

## Scope

This document defines the native Engineering Flow Platform (EFP) runtime contract exposed to Portal and integration smoke suites.

The native runtime is an API-only service. It does not serve a built-in browser page, root HTML route, bundled asset routes, template files, or frontend assets; Portal is the UI.

## Required HTTP Surface

Native runtime must support:

- `GET /health`
- `GET /actuator/health`
- `GET /ready`
- `POST /api/chat`
- `POST /api/chat/stream`
- `GET /api/events`
- `GET /api/capabilities`
- `GET /api/skills`
- `POST /api/tasks/execute`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/cancel`
- `GET /api/usage`
- `GET /api/sessions`

## Runtime Profile Boot Contract

Runtime-profile configuration is delivered exclusively through pod environment
variables injected by Portal from the per-profile Secret; there is no runtime
apply endpoint and no hot-apply path. Config changes reach a pod only via a
Portal-triggered restart with a new Secret.

- `EFP_PROFILE_CONFIG`: full apply-payload JSON
  (`{"runtime_profile_id", "name", "revision", "runtime_type", "config"}`).
  Parsed once at process start and merged in memory over the read-only base
  `config.yaml`. A missing variable means dev mode (base config only); an
  empty `config` object is a valid empty profile. After boot projection the
  runtime scrubs `EFP_PROFILE_CONFIG` from `os.environ` before any child
  process can spawn.
- `EFP_PROFILE_REVISION`: profile revision string from the same Secret.
- `EFP_PROFILE_ID`: bound profile id, or `none` for unbound agents.
- `EFP_CONFIG_JSON`: exported by the runtime itself after projection — a
  tools `RootConfig`-shaped JSON (`version`/`jira`/`confluence`/`jenkins`/
  `aws`/`visual`/`mobile-auto`) read by every CLI child process.
- `GET /ready` returns `200 {"ready": true, "runtime_profile_id", "revision"}`
  only after the boot projection succeeded, `503 {"ready": false, "error"}`
  otherwise. `GET /health` stays always-ok as the liveness probe.

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
- Runtime profile boot projection applies GitHub, AWS, and Git configuration through real CLIs and exports Jira, Confluence, Jenkins, mobile BrowserStack, and visual configuration to CLI child processes via `EFP_CONFIG_JSON`.
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
