# Storage Paths - Unified ~/.efp/ Directory

All persistent data is stored under `~/.efp/` for easy backup and management.

## Directory Structure

```
~/.efp/
├── sessions/              ← Session persistence (JSONL)
│   ├── sessions.active.jsonl
│   └── archive/
│       └── sessions_YYYYMMDD_HHMMSS.jsonl
│
├── memory/                ← Long-term memory (SQLite)
│   └── memories.sqlite
│
└── workspace/             ← Project-specific files
    ├── MEMORY.md
    ├── SOUL.md
    ├── USER.md
    ├── AGENTS.md
    └── memory/
        └── YYYY-MM-DD.md
```

## Storage Details

| Path | Type | Description |
|------|------|-------------|
| `~/.efp/sessions/` | JSONL | Session transcripts with TTL |
| `~/.efp/memory/` | SQLite | Memory chunks with FTS5 search |
| `~/.efp/workspace/` | Markdown | Project-specific context files |

## Configuration

Storage paths are configured in `config.yaml`:

```yaml
session:
  persistence:
    storage_dir: "~/.efp/sessions"

memory:
  path: "~/.efp/memory"
  workspace: "~/.efp/workspace"

workspace:
  path: "~/.efp/workspace"
  sessions_dir: "~/.efp/sessions"
```

## Backup

To backup all data:

```bash
# Backup entire ~/.efp/ directory
cp -r ~/.efp ~/efp_backup_$(date +%Y%m%d)
```

## First Run Setup

On first run, the platform will create:

1. `~/.efp/sessions/` - Session storage directory
2. `~/.efp/memory/` - Memory database directory
3. `~/.efp/workspace/` - Workspace directory

Copy template files from `workspace/*.example` to `~/.efp/workspace/` if needed:

```bash
cp workspace/*.example ~/.efp/workspace/
```
