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

stage_browserstack_local() {
  local source="$BROWSERSTACK_LOCAL_SOURCE"
  if [[ -z "$source" ]]; then
    log "BrowserStackLocal source not set; private-managed mobile runs require staging runtime-tools/BrowserStackLocal separately"
    return
  fi
  if [[ "$source" != /* ]]; then
    source="$ROOT/$source"
  fi
  [[ -f "$source" ]] || die "BrowserStackLocal source does not exist: $source"
  install -m 0755 "$source" "$OUTPUT_DIR/BrowserStackLocal"
  log "Staged BrowserStackLocal binary from $source"
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
log "Built runtime tools: ${tool_names[*]}"
log "Prepared runtime tool binaries in $OUTPUT_DIR"
