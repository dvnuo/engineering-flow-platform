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
- `GET /api/copilot/quota`
- `GET /api/models`
- `GET /api/sessions`
- `POST /api/files/upload` and `POST /api/files/parse` (chatbox attachments;
  `GET /api/files/{file_id}/preview`, `GET /api/files/{file_id}` and
  `DELETE /api/files/{file_id}` complete the set). The accepted extensions
  come from `EFP_CHAT_UPLOAD_EXTENSIONS` (default
  `pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml,doc,dot,xls,png,jpg,jpeg,jpe,jfif,tiff,webp,bmp`,
  the formats Productivity Suite takes; images, and the office formats the
  runtime does not parse (doc, dot, xls), are stored and handed to the agent
  instead of parsed, see Connectors below) and the size cap from
  `EFP_MAX_UPLOAD_MB`; the Portal sets both on the pod from its own settings.

## Usage, Credits and Budget Events

Copilot bills per token in GitHub AI credits (1 credit = $0.01); the runtime
estimates them from GitHub's published prices (`src/efp_runtime/llm/pricing.py`,
two tiers: the GPT-6 line costs about twice as much per token once a request's
prompt exceeds 272k input tokens) and reports them wherever it reports usage.

- The chat response's `usage` (and the stored final assistant message's
  `metadata.run_usage`) carries, next to `input_tokens` / `output_tokens` /
  `reasoning_tokens` / `cached_input_tokens` / `total_tokens`:
  `estimated_credits` (float or null), `iterations` (model calls in the run),
  `cache_hit_ratio` (cached / input, or null) and `pricing`
  `{model, tier: "default"|"long_context", threshold_tokens, source:
  "static"|"catalog"|"copilot_usage", tiers_seen}`. The `usage.updated` event
  adds `estimated_credits` and `pricing` per step. When Copilot reports its
  own charge (`copilot_usage.total_nano_aiu`, 1 credit = 10^9 nano-AIU, the
  number VS Code shows per request; it sits on the `response.completed` event
  next to `response`, or inside the usage object) that charge *is*
  `estimated_credits` for the step and the run: `actual_credits` carries it
  separately, `pricing.source` is `copilot_usage` (the table's figure rides
  along as `pricing.estimated_credits`) and the run's usage says
  `credits_source: "copilot_usage"` (else `"estimate"`).
- The chat response (and the stream's `final` event) carries
  `user_message_id`, the id of the stored user turn the run answered, and
  `assistant_message_id`, so the Portal can attach its per-message actions
  (edit, retry, delete from here) to the bubble it drew when the member sent
  the message, without reloading the transcript.
- `context_usage` (event and `GET /api/sessions/{id}/context-usage`) adds
  `budget_tokens` (the prompt budget the runner enforces, estimated tokens),
  `billing_threshold_tokens`, `billing_tier`, `calibration`
  `{ratio, chars_per_token, last_input_tokens, samples}` (how far the chars/4
  estimate is from the provider's real counts), `calibrated_used_tokens` and
  `pruned_tool_outputs {count, chars}`.
- `request_tool_outputs_pruned` (once per run): old tool results were
  shortened in the model request; the transcript is unchanged.
- `run_credits_limit_reached`: the run's estimated credits passed
  `run_credits_limit`; the next iteration is the summary turn.
- `run_budget_paused` `{reason: "credits_guard"|"iteration_cap", iterations,
  iteration_limit, estimated_credits, credits_limit}`: the run ended with a
  summary because of the credits guard or the step cap; the response's
  `terminal_reason` is `budget_paused` for the guard (`max_steps_reached` for
  the cap). The member continues by sending a message.
- `GET /api/usage` totals add `total_cached_input_tokens`,
  `total_reasoning_tokens`, `total_estimated_credits` (also per model).
- `GET /api/copilot/quota` answers 200 with `{available, source, fetched_at,
  plan, reset_date, reset_date_utc, token_based_billing, snapshots: [{id,
  label, entitlement, remaining, used, credits_used, percent_remaining,
  unlimited, has_quota, token_based_billing, reset_at, overage_count,
  overage_permitted}], credits, live, error}` from GitHub's undocumented
  `copilot_internal/user` endpoint for the runtime's GitHub token, cached 60 s
  (`?refresh=1` bypasses); `available: false` with `error` when it cannot be
  asked. Under usage-based billing (`token_based_billing: true`, every plan
  since 2026-06-01) the `premium_interactions` snapshot counts AI credits and
  is projected as `credits` `{id, label: "AI credits", included, used,
  remaining, percent_remaining, unlimited, pooled, has_quota, exhausted,
  overage_count, overage_permitted, reset_at}`: a per-seat allotment has
  `included` / `remaining`; a pooled organization allotment is `unlimited`
  per user (`included` and `remaining` null), `used` is `credits_used` and
  `exhausted` means `has_quota: false`, the pool is spent. `live` is the quota
  snapshot header of the latest chat answer (`{id, entitlement, unlimited,
  percent_remaining, overage_count, overage_permitted, reset_date,
  observed_at}` or null), fresher than the endpoint's cached answer.
- `GET /api/models` answers 200 with `{available, source: "catalog"|"static",
  fetched_at, provider, default_model, auto: [{id, label, tier}], models:
  [{id, name, vendor, version, preview, selectable, reasons, picker_enabled,
  policy, category, limits: {context_window_tokens, prompt_tokens,
  output_tokens}, supports: {tool_calls, vision, parallel_tool_calls,
  streaming, structured_outputs, reasoning_efforts}, endpoints, pricing:
  {default|long_context: {prompt_cap_tokens, input, output, cached_input,
  cache_write} (credits per million tokens)}, billing_threshold_tokens}],
  selectable: [ids], error}`. `source: "catalog"` is Copilot's own `GET
  /models` for the runtime's credential (what VS Code's picker shows: the
  organization's model policy applied), cached for an hour (`?refresh=1`
  bypasses); a model is `selectable` when it is a chat model served on
  `/responses` with tool calls, policy enabled and shown in the picker, and
  selectable ids are accepted as `requested_model` / `model_override` next to
  the static list (their limits and prices feed the budget and the credits
  estimate). `source: "static"` is the hand-maintained list, returned without
  a credential or when the catalog cannot be fetched (`error` says why).

## Following a Chat Run

A page that did not start a run, or lost its chat stream, joins the run in
flight from the runtime's memory of it; nothing of this is persisted.

- Every event of a chat run carries `run_seq` (1, 2, 3... per request id; a
  run resumed after a card answer has its own request id and starts at 1
  again), the same number on `/api/chat/stream` and on `/api/events`.
- The run registry keeps a journal of each run's events: at most 2,000
  records or 2 MiB, consecutive text and tool-input deltas of one part merged
  into one record, the oldest records dropped past the cap; kept 5 minutes
  after the run ends and lost on a restart. A merged record served to a viewer
  that has part of it covers only what the viewer lacks: `delta` is the text
  from `run_seq_from` to `run_seq`, and its `id` is `<id>@<from>-<to>`.
- `GET /api/sessions/{session_id}` adds `live_run`: `{request_id, state,
  started_at, last_seq}` for the run in flight (`state` running or
  cancelling), `null` when none is, after a restart included. It is read
  before the history, so a run that ends meanwhile is still announced and
  found finished when joined.
- `GET /api/events?session_id=...&request_id=...&after_seq=N` attaches the
  socket to that run. The welcome (`type: "connected"`) carries `attach:
  {request_id, found, state, after_seq}`, plus `first_seq` and `last_seq` when
  the journal is there; then come the journal's events after `N`, then the
  run's live events, each once and in order. In this mode there is no replay
  by time (`replay`, `replay_limit` and `last_event_at` are ignored), even when
  `found` is false: a run that has not started yet streams from its first
  event. When the journal cannot supply what follows the viewer's position (it
  dropped its oldest records, or it is gone), `{"type": "resync", "data":
  {request_id, reason: "journal_truncated"|"journal_unavailable", first_seq}}`
  says that the events before `first_seq` are not coming. Without `after_seq`
  the socket behaves as before.

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
- The runtime image may include prebuilt `engineering-flow-platform-tools` CLI binaries on `PATH` in `/usr/local/bin`. Current binaries include `jira`, `confluence`, `jenkins`, `aws-auth`, `nexus`, `splunk`, `pgsql`, `browser`, and `mobile-auto`; future binaries are discovered from `cmd/<tool>` in that repo. The image also carries the AWS CLI v2, `kubectl` (pinned to a minor release line through `KUBECTL_STABLE_CHANNEL`/`KUBECTL_VERSION`), and `jq`; the AWS login provider `aws-auth login` shells out to (`adfs-assume` or `saml2aws`, per `aws.provider`) is staged into `runtime-tools/` by `scripts/prepare-runtime-tools.sh` from `ADFS_ASSUME_SOURCE`/`SAML2AWS_SOURCE` and installed alongside.
- AWS access is per account: `aws-auth login --account <name>` writes each configured account's credentials to the AWS CLI profile named after the account, and `aws-auth eks kubeconfig --account <name> --cluster <cluster>` writes a `<account>/<cluster>` kubectl context. The profile's `aws.accounts[]` matrix, provider, and defaults reach the CLI through `EFP_AWS_*`; the `assume-role` provider needs no directory password, so the boot projection skips `aws-auth auth login` for it.
- The troubleshooting CLIs `nexus`, `splunk`, and `pgsql` are configured through the profile sections of the same names. `nexus` and `splunk` are read-only binaries; `pgsql` writes only when the section's `access` is `read_write` (absent reads as `read_only`, in which mode the CLI refuses every write before connecting), and then runs whatever the configured database role and endpoint allow, so a profile that should only read keeps the default access and is given a read-only role or a replica endpoint. `nexus`/`splunk` are projected like jira (`EFP_NEXUS_INSTANCES_0_BASE_URL`, `EFP_SPLUNK_INSTANCES_0_AUTH_TOKEN`, `EFP_SPLUNK_INSTANCES_0_APP`, ...) with an empty `rest_path` because each CLI owns its REST prefix; `pgsql` is copied verbatim: the shared settings (`EFP_PGSQL_ACCESS`, `EFP_PGSQL_SSLMODE`, `EFP_PGSQL_STATEMENT_TIMEOUT_SECONDS`, `EFP_PGSQL_MAX_ROWS`) and the rows (`EFP_PGSQL_INSTANCES_0_HOST`, `_DATABASE`, `_USERNAME`, `_PASSWORD`), with each row's `proxy` filled from the Proxy connector's assignment. The CLI instruction text names all three when any is enabled.
- `bash` output is passed through the runtime's secret redaction before it reaches the model and before the full output is archived under `.efp_runtime/tool-output/`; `bash` also exposes the command string as its permission subject, so `tool_permissions.bash` accepts subject globs such as `{"*kubectl*delete*": "deny", "*": "allow"}`.
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
  `connectors.local_bridge` is present. The gateway registers the `browser`
  tool only for interactive chats that carry that block; background tasks,
  Jira/GitHub handlers, and sub-agents never see it.
- The `browser` tool publishes `tool.connector_requested` on the runtime event
  bus while it waits (projected to `connector.request` on `/api/events`, with
  `data.session_id`, `data.request_id`, `data.connector_type`, and
  `data.connector_request.{id, action, params, target_client_id, timeout_seconds}`),
  then blocks on the process-wide `ConnectorBridgeBroker`.
- `timeout_seconds` on the request is the bridge's budget for the command
  (5-120 s). The tool waits that long plus a grace (30 s, plus one second per
  MiB of files the page stages first) before answering `connector_timeout`,
  so the bridge's own `timeout` or `workflow_failed` normally arrives first;
  `connector_timeout` means the page did not answer at all.
- When a chat turn parks on a question or permission card, the session keeps
  the turn's trusted `connectors` (and `enable_browser_tool`) as
  `pending_input_context` in session metadata. An answer (`question/respond`,
  `permission/respond`) whose trusted metadata names no `connectors` resumes
  the turn with those, so the resumed run keeps the `browser` tool; an answer
  that names connectors wins, since it says which tab is answering. The
  context is dropped once a run ends without a pending card.
- An accepted answer retires the card at once: before `question/respond` or
  `permission/respond` returns `202`, `pending_question_request` /
  `pending_permission_request` (and `pending_input_context`) leave session
  metadata, so `GET /api/sessions/{session_id}/pending-input` reports nothing
  while the resumed run is in flight and a page refresh does not rebuild the
  card. The answer itself stays as `pending_question_answer` until the tool
  call it answers has a result; the run's end records whatever it stops on
  next, including the same question re-raised. A second answer while the run
  is in flight is refused with `409`.
- For `page.upload` the request's `params.files` is `[{path, name, bytes}]`
  (workspace-relative paths the tool checked; never file contents), and for
  `workflow.run` it is `{workflow_yaml, vars, files: [{var, path, name, bytes}],
  yes, dry_run, continue_on_error, target_id?}`; the page pulls each file
  through the Server files download, stages it on the bridge, and runs the
  command with the staged ids.
- Chat attachments the model cannot read (images, unless the chat provider
  can see them; doc, dot, xls) are listed in an "Attached files" block in
  the prompt (`src/gateway/attachment_handoff.py`). When the trusted metadata
  carries `connectors.local_bridge` and it is not disabled, the block points
  the agent at the attached-files analysis skill (Productivity Suite through
  `workflow.run`); otherwise it says the files cannot be analyzed in this
  chat and never to guess.
- The Portal page answers with
  `POST /api/sessions/{session_id}/connectors/respond`
  `{request_id, client_id, ok, result | error}` → `202`; an unknown, foreign,
  timed-out, or already answered id → `409 connector_request_not_pending`.
  `GET /api/sessions/{session_id}/connectors/pending` lists what a session is
  still waiting on. The full wire contract lives in the Portal repository as
  `docs/CONNECTORS_CONTRACT.md`.
