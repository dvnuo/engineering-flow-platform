#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT/runtime-tools"
TOOLS_REPO_URL="https://github.com/dvnuo/engineering-flow-platform-tools.git"
TEMP_DIR=""
TOOLS_REPO_DIR=""
TARGET_GOOS="${GOOS:-linux}"
TARGET_GOARCH="${GOARCH:-amd64}"
TARGET_CGO_ENABLED="${CGO_ENABLED:-0}"
BROWSERSTACK_LOCAL_SOURCE="${BROWSERSTACK_LOCAL_SOURCE:-${BROWSERSTACK_LOCAL_BINARY:-}}"
# AWS login providers that `aws-auth login` shells out to. Neither is built from
# the tools repo: adfs-assume is an enterprise binary, saml2aws a GitHub release
# (https://github.com/Versent/saml2aws/releases). Stage whichever the runtime
# profile's aws.provider selects; both are optional at build time.
ADFS_ASSUME_SOURCE="${ADFS_ASSUME_SOURCE:-}"
SAML2AWS_SOURCE="${SAML2AWS_SOURCE:-}"

log() {
  printf '[prepare-runtime-tools] %s\n' "$*" >&2
}

die() {
  printf '[prepare-runtime-tools] ERROR: %s\n' "$*" >&2
  exit 1
}

cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf "$TEMP_DIR"
  fi
}
trap cleanup EXIT

resolve_tools_repo_dir() {
  local candidate
  if [[ -n "${EFP_TOOLS_REPO_DIR:-}" ]]; then
    candidate="$EFP_TOOLS_REPO_DIR"
    if [[ "$candidate" != /* ]]; then
      candidate="$ROOT/$candidate"
    fi
    [[ -d "$candidate" ]] || die "EFP_TOOLS_REPO_DIR does not exist: $candidate"
    TOOLS_REPO_DIR="$candidate"
    return
  fi

  candidate="$(cd "$ROOT/.." && pwd)/engineering-flow-platform-tools"
  if [[ -d "$candidate" ]]; then
    TOOLS_REPO_DIR="$candidate"
    return
  fi

  command -v git >/dev/null 2>&1 || die "git is required to clone engineering-flow-platform-tools"
  TEMP_DIR="$(mktemp -d)"
  log "Cloning engineering-flow-platform-tools into a temporary directory"
  git clone --depth 1 "$TOOLS_REPO_URL" "$TEMP_DIR/engineering-flow-platform-tools"
  TOOLS_REPO_DIR="$TEMP_DIR/engineering-flow-platform-tools"
}

# stage_provided_binary <name> <source-path> <why-it-matters>
# Copies a third-party binary that the runtime image must carry but this repo
# does not build. An unset source only logs what the image will lack.
stage_provided_binary() {
  local name="$1" source="$2" reason="$3"
  if [[ -z "$source" ]]; then
    log "$name source not set; $reason"
    return
  fi
  if [[ "$source" != /* ]]; then
    source="$ROOT/$source"
  fi
  [[ -f "$source" ]] || die "$name source does not exist: $source"
  install -m 0755 "$source" "$OUTPUT_DIR/$name"
  log "Staged $name binary from $source"
}

stage_browserstack_local() {
  stage_provided_binary BrowserStackLocal "$BROWSERSTACK_LOCAL_SOURCE" "private-managed mobile runs require staging runtime-tools/BrowserStackLocal separately"
}

stage_aws_login_providers() {
  stage_provided_binary adfs-assume "$ADFS_ASSUME_SOURCE" "aws-auth login with provider adfs-assume will report provider_missing unless the binary is installed another way"
  stage_provided_binary saml2aws "$SAML2AWS_SOURCE" "aws-auth login with provider saml2aws will report provider_missing unless the binary is installed another way"
}

command -v go >/dev/null 2>&1 || die "go is required to build runtime tools"
mkdir -p "$OUTPUT_DIR"

resolve_tools_repo_dir
[[ -f "$TOOLS_REPO_DIR/go.mod" ]] || die "tools repo is missing go.mod: $TOOLS_REPO_DIR"
[[ -d "$TOOLS_REPO_DIR/cmd" ]] || die "tools repo is missing cmd directory: $TOOLS_REPO_DIR"

log "Using tools repo: $TOOLS_REPO_DIR"
log "Target platform: GOOS=$TARGET_GOOS GOARCH=$TARGET_GOARCH CGO_ENABLED=$TARGET_CGO_ENABLED"

tool_names=()
while IFS= read -r -d '' main_go; do
  tool_names+=("$(basename "$(dirname "$main_go")")")
done < <(find "$TOOLS_REPO_DIR/cmd" -mindepth 2 -maxdepth 2 -type f -name main.go -print0 | sort -z)

if [[ "${#tool_names[@]}" -eq 0 ]]; then
  die "no runtime tools found under $TOOLS_REPO_DIR/cmd/*/main.go"
fi

log "Discovered runtime tools: ${tool_names[*]}"

# runtime-tools/ is a generated Docker build input. Keep README.md, but remove
# stale binaries so deleted or renamed cmd/<tool> directories do not enter PATH.
find "$OUTPUT_DIR" -maxdepth 1 -type f ! -name README.md -delete

built_outputs=()
for tool_name in "${tool_names[@]}"; do
  output_path="$OUTPUT_DIR/$tool_name"
  log "Building $TARGET_GOOS/$TARGET_GOARCH $tool_name binary"
  (
    cd "$TOOLS_REPO_DIR"
    CGO_ENABLED="$TARGET_CGO_ENABLED" GOOS="$TARGET_GOOS" GOARCH="$TARGET_GOARCH" \
      go build -o "$output_path" "./cmd/$tool_name"
  )
  built_outputs+=("$output_path")
done

chmod 0755 "${built_outputs[@]}"
stage_browserstack_local
stage_aws_login_providers
log "Built runtime tools: ${tool_names[*]}"
log "Prepared runtime tool binaries in $OUTPUT_DIR"
