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
- `POST /api/files/upload` and `POST /api/files/parse` (chatbox attachments;
  `GET /api/files/{file_id}/preview`, `GET /api/files/{file_id}` and
  `DELETE /api/files/{file_id}` complete the set). The accepted extensions
  come from `EFP_CHAT_UPLOAD_EXTENSIONS` (default
  `pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml`; images are
  supported but only offered when a deployment adds them for a model with
  vision) and the size cap from `EFP_MAX_UPLOAD_MB`; the Portal sets both
  on the pod from its own settings.

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
- Tools config env vars: exported by the runtime itself after projection —
  EFP_-prefixed, indexed environment variables flattened from the tools
  `RootConfig`-shaped subset (`version`/`jira`/`confluence`/`jenkins`/`aws`/
  `mobile-auto`) of the effective config. Each scalar leaf becomes the
  literal prefix `EFP_` plus an UPPERCASED `_`-joined path from the root (with
  `-` replaced by `_` and list elements indexed by position), e.g.
  `EFP_JIRA_DEFAULT_INSTANCE`, `EFP_JIRA_INSTANCES_0_BASE_URL`, `EFP_AWS_DOMAIN`,
  `EFP_AWS_ACCOUNTS_0_ACCOUNT_ID`, `EFP_AWS_ACCOUNTS_0_REGIONS_0`,
  `EFP_MOBILE_AUTO_BROWSERSTACK_USERNAME`. Only present values are emitted, and
  every CLI child process reads them.
- `KUBECONFIG`: set by the runtime when the profile enables `aws` (an inherited
  value is respected), pointing at `aws.kubeconfig_path` or
  `~/.efp/kube/config`, outside the workspace. `aws-auth eks kubeconfig` writes
  `<account>/<cluster>` contexts there and `kubectl` reads them.
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
- Interactive chats also get `session_search`, which searches and reads the assistant's other stored sessions under the runtime session root (member and assistant text only). It scopes to the Portal member by default (`scope=mine`, the header-derived `portal_user_id` from `X-Portal-User-Id` matched against the `author_id` stamped on member turns) and to the whole agent on request (`scope=agent`). Without a member identity `mine` fails closed, and reading another member's session always needs `scope=agent`. The Portal stamps the identity headers on every proxied request, and the gateway passes them on the question/permission answer and edit-regenerate resumes as well as on chat. A runtime profile can disable it with `enable_session_search: false`.
- Interactive chats also get `memory`, which keeps up to 50 one-sentence standing notes per Portal member under `<session root>/memory/<member>.json` and renders them into the system prompt as "Member notes". Notes are written only when the model calls `remember`, never extracted automatically; the tool refuses without a member identity (the same header-derived `portal_user_id`, never a body-supplied `portal_user`). A runtime profile can disable it with `enable_member_memory: false`.
- Legacy Python tool packages such as `src.bash_tools` are not present, and Jira/GitHub/Confluence/Git Python tools are not exposed as LLM tools.
- The runtime image may include prebuilt `engineering-flow-platform-tools` CLI binaries on `PATH` in `/usr/local/bin`. Current binaries include `jira`, `confluence`, `jenkins`, `aws-auth`, `browser`, and `mobile-auto`; future binaries are discovered from `cmd/<tool>` in that repo. The image also carries the AWS CLI v2, `kubectl` (pinned to a minor release line through `KUBECTL_STABLE_CHANNEL`/`KUBECTL_VERSION`), and `jq`; the AWS login provider `aws-auth login` shells out to (`adfs-assume` or `saml2aws`, per `aws.provider`) is staged into `runtime-tools/` by `scripts/prepare-runtime-tools.sh` from `ADFS_ASSUME_SOURCE`/`SAML2AWS_SOURCE` and installed alongside.
- AWS access is per account: `aws-auth login --account <name>` writes each configured account's credentials to the AWS CLI profile named after the account, and `aws-auth eks kubeconfig --account <name> --cluster <cluster>` writes a `<account>/<cluster>` kubectl context. The profile's `aws.accounts[]` matrix, provider, and defaults reach the CLI through `EFP_AWS_*`; the `assume-role` provider needs no directory password, so the boot projection skips `aws-auth auth login` for it.
- Agents use those CLIs through the model-visible `bash` built-in in the workspace-full-access runtime workspace. They should run `<tool> commands --json`, then `<tool> schema <command> --json`, prefer `--json`, use `--dry-run` before writes, and pass `--yes` for destructive operations.
- Runtime profile boot projection applies GitHub, AWS, and Git configuration through real CLIs and exports Jira, Confluence, Jenkins, and mobile BrowserStack configuration to CLI child processes via EFP_-prefixed indexed tools config env vars (e.g. `EFP_JIRA_INSTANCES_0_BASE_URL`, `EFP_AWS_DOMAIN`, `EFP_MOBILE_AUTO_BROWSERSTACK_USERNAME`).
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

## Connectors (Portal-side capabilities)

- Portal may inject `metadata.connectors` (a map keyed by connector type) into
  trusted chat metadata, plus `enable_browser_tool: true` when
  `connectors.local_browser` is present. The gateway registers the `browser`
  tool only for interactive chats that carry that block; background tasks,
  Jira/GitHub handlers, and sub-agents never see it.
- The `browser` tool publishes `tool.connector_requested` on the runtime event
  bus while it waits (projected to `connector.request` on `/api/events`, with
  `data.session_id`, `data.request_id`, `data.connector_type`, and
  `data.connector_request.{id, action, params, target_client_id, timeout_seconds}`),
  then blocks on the process-wide `ConnectorBridgeBroker`.
- The Portal page answers with
  `POST /api/sessions/{session_id}/connectors/respond`
  `{request_id, client_id, ok, result | error}` → `202`; an unknown, foreign,
  timed-out, or already answered id → `409 connector_request_not_pending`.
  `GET /api/sessions/{session_id}/connectors/pending` lists what a session is
  still waiting on. The full wire contract lives in the Portal repository as
  `docs/CONNECTORS_CONTRACT.md`.
