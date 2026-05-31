#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="$ROOT/runtime-tools"
TOOLS_REPO_URL="https://github.com/dvnuo/engineering-flow-platform-tools.git"
TEMP_DIR=""
TOOLS_REPO_DIR=""

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

command -v go >/dev/null 2>&1 || die "go is required to build runtime tools"
mkdir -p "$OUTPUT_DIR"

resolve_tools_repo_dir
[[ -f "$TOOLS_REPO_DIR/go.mod" ]] || die "tools repo is missing go.mod: $TOOLS_REPO_DIR"

log "Using tools repo: $TOOLS_REPO_DIR"
log "Building Linux amd64 jira binary"
(
  cd "$TOOLS_REPO_DIR"
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o "$ROOT/runtime-tools/jira" ./cmd/jira
)

log "Building Linux amd64 confluence binary"
(
  cd "$TOOLS_REPO_DIR"
  CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o "$ROOT/runtime-tools/confluence" ./cmd/confluence
)

chmod 0755 "$ROOT/runtime-tools/jira" "$ROOT/runtime-tools/confluence"
log "Prepared runtime tool binaries in $OUTPUT_DIR"
