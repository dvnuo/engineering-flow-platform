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
- `GET /api/personalization` and `GET /api/workspace/repos` (see Starter
  Cards and Workspace Repositories below)
- `POST /api/files/upload` and `POST /api/files/parse` (chatbox attachments;
  `GET /api/files/{file_id}/preview`, `GET /api/files/{file_id}` and
  `DELETE /api/files/{file_id}` complete the set). The accepted extensions
  come from `EFP_CHAT_UPLOAD_EXTENSIONS` (default
  `pdf,docx,xlsx,csv,txt,log,pptx,zip,md,yaml,yml,json,xml,doc,dot,xls,png,jpg,jpeg,jpe,jfif,tiff,webp,bmp`,
  the formats Productivity Suite takes; images, and the office formats the
  runtime does not parse (doc, dot, xls), are stored and handed to the agent
  instead of parsed, see Image Analysis and Attached Files below) and the size
  cap from `EFP_MAX_UPLOAD_MB`, which is also the largest image `inspect-image`
  takes; the Portal sets both on the pod from its own settings.
  `POST /api/files/parse` with `options.vision_enabled` sends an image to the
  chat model only when the deployment switch `EFP_CHAT_VISION` is on; with the
  switch off (the default) the option falls back to OCR.

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

## Starter Cards and Workspace Repositories

`GET /api/personalization` answers `{"welcome": str|null, "cards": [...]}`
from `portal/welcome.md` and `portal/cards.yaml` at the workspace root (the
agents-repo branch the assistant booted with). Both files are optional, and
whatever is invalid in them is dropped, never an error: at most 12 cards,
text fields cut at 500 characters, prompts and templates at 4000.

- A card is `{title, description, icon, prompt, input?}`. `prompt` is the
  legacy form and uses `{{input}}`; `input` is `{label, placeholder}` plus,
  when valid, `source` (a picker the Portal knows, such as `jira.issues`;
  lower-case dotted name of at most 64 characters), `params` (filters for the
  source) and `multiline` (only the boolean `true`). An input whose `source`
  is invalid stays a plain text input.
- `params` is a mapping of at most 8 entries: keys are lower-case
  identifiers of at most 32 characters; a value is a string (at most 200
  characters), a boolean, an integer, or a list of at most 10 strings (at
  most 100 characters each). An entry with a bad key or value is dropped, and
  a mapping left empty is omitted.
- `?v=2` (a whole number of 2 or more) adds the form of a card: `fields` (1
  to 6) and the `template` their values are placed in, by `{{name}}`. A field
  is `{name, label, type, placeholder, required}` with `type` one of `text`
  (default), `textarea`, `choice`, `file`; a `text` field may carry `source`
  and `params`, a `choice` carries `options` (`[{value, label}]`, at most 20)
  and `multiple`, a `file` may carry `accept` (extensions such as `.csv`, at
  most 8), and any field may carry `default` (a string or a list of at most
  20 strings; for a `choice`, only option values, and only the first unless
  `multiple`). The two keys are valid together or dropped together: a name
  that is missing, repeated or not a lower-case identifier, an unknown
  `type`, a `choice` without a valid option, more than 6 fields, an empty
  `template`, or a `{{name}}` in the template that no field declares.
- Without `v`, or with any other value, `fields` and `template` are never
  sent and a card that has no legacy `prompt` is left out, so a Portal that
  predates the form renders exactly what it did. With `?v=2` such a card is
  sent with `prompt: ""` and no `input`; a card that has both forms carries
  both.

`GET /api/workspace/repos` answers `200 {"repos": [...], "truncated": bool}`,
the git repositories cloned in the workspace, for the Portal's branch picker.
It is read-only and takes no parameter.

- An entry is `{path, name, remote_url, host, repo, branch, head, upstream,
  default_branch, ahead, dirty, last_commit_at}`. `path` is relative to the
  workspace, with forward slashes. `remote_url` is the origin URL without its
  userinfo, so a token in a clone URL never leaves the pod; `host` (no port,
  lower case) and `repo` (the path without the leading slash and the trailing
  `.git`) are read from it for https, `ssh://` and `git@host:owner/name.git`
  remotes, and are "" without an origin or for a local path.
- `branch` is "" on a detached HEAD, `upstream` "" when the branch tracks
  nothing, `default_branch` what `refs/remotes/origin/HEAD` names ("" when
  unknown). `ahead` counts the commits of `HEAD` that the upstream, else
  `origin/<default_branch>`, does not have, and is `null` when there is
  neither or the count fails. `dirty` is whether tracked files have changes,
  `null` when that could not be told in time. `last_commit_at` is the
  committer time of `HEAD` in UTC (ISO 8601), `null` before the first commit.
- A repository is a directory holding a `.git` entry in one of `repos/*`,
  `repos/*/*`, `.efp_runtime/repositories/*` or the direct children of the
  workspace root; nothing else is scanned, the root itself is never listed,
  and a directory that resolves outside the workspace is skipped.
- At most 30 repositories, newest `last_commit_at` first. Only local,
  read-only git commands run, each within 3 seconds and all within 8;
  `truncated` is true when that budget ran out or more than 30 repositories
  were found. A directory git cannot read is skipped.

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
- `EFP_PORTAL_TRUST_TOKEN`: per-assistant secret, not part of the profile. The
  Portal sends it as `X-Portal-Trust-Token` on every request; while it is set
  the runtime answers `401` to any request without it (except `/health`,
  `/actuator/health`, `/ready` and loopback callers) and sends it on its calls
  to the Portal's `/api/internal/*` API. Unset means no check. See
  `docs/control_plane_contract.md`.
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
- `INSPECT_IMAGE_CONFIG` and `EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME` /
  `_PASSWORD` / `_USERCASE`: exported by the runtime when the profile's
  `image_analysis` section (the Portal's Image analysis connector) is
  configured, see Image Analysis and Attached Files below. A failure to
  project it fails the boot like any other projection.
- `GET /ready` returns
  `200 {"ready": true, "runtime_profile_id", "revision", "image_analysis", "proxy"}`
  only after the boot projection succeeded, `503 {"ready": false, "error"}`
  otherwise. `image_analysis` is `{configured, reason, model, config_path}`
  (never the account) and `proxy` the resolved Proxy connector without
  credentials. `GET /health` stays always-ok as the liveness probe.

## Image Analysis and Attached Files

Every chat model is treated as having no vision, whichever provider the
profile chats on (GitHub Copilot or AI Platform).

- Default: no chat request carries image parts. `EFP_CHAT_VISION` truthy
  (`1`, `true`, `yes`, `on`) in the pod's environment turns inlining on, for
  any provider; `EFP_COPILOT_VISION_VIA_CHAT` is the older name of the same
  switch and is still read. It is an operator's switch that the Portal writes
  into every agent pod from its own setting of the same name; there is no
  member-facing control. The file parser's vision path
  (`parse_image_with_vision`, reached through `POST /api/files/parse` with
  `options.vision_enabled`) obeys the same switch.
- An image the chat model cannot see is read, in this order: with the
  `inspect-image` CLI when image analysis is configured; else, in an
  interactive chat with the Local bridge on, through Productivity Suite (the
  attached-files analysis skill); else the agent says which files it could not
  read. When `inspect-image` answers `auth_required` or `config_error`, or
  still fails after one retry, the agent falls back to Productivity Suite if
  the Local bridge is on, and otherwise says the Image analysis connector
  needs checking. The office formats the runtime does not parse (doc, dot,
  xls) are not images: Productivity Suite is their only route.
- The connector is the profile section `image_analysis`, independent of `llm`
  (it is read whatever `llm.provider` is, and `llm.ai_platform` is never read
  for it): `{enabled, model, ai_platform: {chat: {host, uri}, ib2b: {host,
  uri}, auth: {username, password, usercase, trust_token_header,
  tracking_prefix}}}`. The Portal fills the endpoints from its deployment
  settings and leaves the section out when the connector is off. It is
  configured when `enabled` is true and `ai_platform.auth.username`,
  `.password`, `.usercase`, `ai_platform.chat.host` and `ai_platform.ib2b.host`
  are all non-empty.
- When configured, boot writes `<directory of config.yaml>/inspect-image.yaml`
  (mode 0600) and exports `INSPECT_IMAGE_CONFIG` (that path) plus
  `EFP_INSPECT_IMAGE_AI_PLATFORM_USERNAME` / `_PASSWORD` / `_USERCASE`, which
  the file references as `${NAME}` so the password never lands on disk. The
  file holds `inspect_image: {provider: ai_platform, defaults: {model},
  limits: {max_image_bytes}}` and `ai_platform: {chat, ib2b, auth}`;
  `model` is `image_analysis.model` coerced to an AI Platform model (default
  `gpt-5.4`), `max_image_bytes` follows `EFP_MAX_UPLOAD_MB`, `auth.token_file`
  is `<that directory>/tmp/inspect-image-ai-platform-token` (removed at every
  boot so a changed account never reuses a token), and `inspect_image.api.proxy`
  is present only when the Proxy connector's assignment for `image_analysis`
  gives one (a proxy URL or `none`). When not configured both files are
  removed and no variable is exported. The file is inspect-image's own
  because the CLI rewrites its config on every token refresh
  (`src/external_cli/inspect_image_config.py`).
- `GET /ready` reports `image_analysis: {configured, reason, model,
  config_path}`. The reasons when not configured: `image analysis is off for
  this profile`, `AI Platform username, password, and usercase are required`,
  `AI Platform chat endpoint is not configured`, `AI Platform iB2B endpoint is
  not configured`.
- A configured profile gets the image-analysis instruction text appended to
  `instruction_texts` (`RUNTIME_PROFILE_IMAGE_ANALYSIS_INSTRUCTIONS` in
  `src/runtime_profile_projection.py`, the same string as the Portal's
  constant). Sub-agents and background tasks inherit `instruction_texts`, so
  they read images with the CLI too; they have no browser and no bridge.
- Every chat attachment is listed in an "Attached files" block in the prompt
  with its workspace path and what of it reached the model
  (`src/gateway/attachment_handoff.py`). When some are not readable by the
  chat model (images while the switch is off; doc, dot, xls), one more line
  follows the list and its rule: a sentence saying so, then, by case (the
  bridge is `connectors.local_bridge` present and not disabled in the trusted
  metadata):

  | Image analysis | Unreadable images | Unreadable office files | What follows |
  |---|---|---|---|
  | configured | yes | no | how to run `inspect-image`; then the fallback: Productivity Suite with the bridge, or tell the user and name the connector without it |
  | configured | yes | yes | the same; then, for the office files, Productivity Suite with the bridge, or that they cannot be analyzed without it |
  | configured | no | yes | Productivity Suite with the bridge, or that the files cannot be analyzed in this chat |
  | not configured | any | any | Productivity Suite with the bridge, or that the files cannot be analyzed in this chat; with unreadable images and no bridge, also that the Image analysis connector would read them |

  The agent is told never to guess what a file shows and never to fall back
  to OCR or image-parsing scripts. With `EFP_CHAT_VISION` on, images are
  listed as sent to the model and nothing points at `inspect-image`.

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
- The runtime image may include prebuilt `engineering-flow-platform-tools` CLI binaries on `PATH` in `/usr/local/bin`. Current binaries include `jira`, `confluence`, `jenkins`, `aws-auth`, `nexus`, `splunk`, `pgsql`, `browser`, and `mobile-auto`, plus `inspect-image` for image analysis (see Image Analysis and Attached Files above); future binaries are discovered from `cmd/<tool>` in that repo, except the ones `scripts/prepare-runtime-tools.sh` leaves out as not for the pod (`EXCLUDED_RUNTIME_TOOLS`, by default `efp-bridge`, which runs on the member's computer). The image also carries the AWS CLI v2, `kubectl` (pinned to a minor release line through `KUBECTL_STABLE_CHANNEL`/`KUBECTL_VERSION`), and `jq`; the AWS login provider `aws-auth login` shells out to (`adfs-assume` or `saml2aws`, per `aws.provider`) is staged into `runtime-tools/` by `scripts/prepare-runtime-tools.sh` from `ADFS_ASSUME_SOURCE`/`SAML2AWS_SOURCE` and installed alongside.
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
- The context also keeps the model the parked turn was asked to use (`model`,
  an auto id included). An answer resumes on the trusted `model_override` in
  its body (the Portal's chat proxy fills in the session's saved run
  settings), else on the kept model, else on the assistant's configured one;
  a kept model the runtime no longer lists counts as none. A resumed run on an
  auto id is routed like any other, on the prompt of the turn it continues
  (the newest message a person wrote), and gets its own session token.
- Every question the `question` tool raises can be answered. An entry with no
  words is left out (its `header` stands in when only that carries them), an
  option with no label is dropped, and `custom: false` is kept only while the
  question still has an option to pick, so a pending request never holds a
  question with neither options nor a text box. A call left with no question
  at all is answered with a `validation_error` result instead of parking the
  run. Answers pair with questions by position in that list. Fewer answers
  than questions is a reply typed in the member's own words (the Portal's
  composer posts one line for the whole card): the tool result hands the
  model the line whole rather than marking the other questions unanswered.
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
- Chat attachments the model cannot read (images, unless the deployment
  switch `EFP_CHAT_VISION` is on; doc, dot, xls) are listed in an "Attached
  files" block in the prompt (`src/gateway/attachment_handoff.py`). Images go
  to the `inspect-image` CLI first when the Image analysis connector is
  configured. For everything else, and as the fallback for an image
  `inspect-image` could not read: when the trusted metadata carries
  `connectors.local_bridge` and it is not disabled, the block points the
  agent at the attached-files analysis skill (Productivity Suite through
  `workflow.run`); otherwise it says the files cannot be analyzed in this
  chat and never to guess. The cases are listed under Image Analysis and
  Attached Files above.
- The Portal page answers with
  `POST /api/sessions/{session_id}/connectors/respond`
  `{request_id, client_id, ok, result | error}` → `202`; an unknown, foreign,
  timed-out, or already answered id → `409 connector_request_not_pending`.
  `GET /api/sessions/{session_id}/connectors/pending` lists what a session is
  still waiting on. The full wire contract lives in the Portal repository as
  `docs/CONNECTORS_CONTRACT.md`.
