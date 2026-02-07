# Memory Tools - Memory Management

Search and retrieve memories from MEMORY.md and daily notes.

## Usage

```bash
memory_search query="project decisions" maxResults=5
memory_get path="MEMORY.md"
memory_get path="memory/2026-02-07.md" fromLine=1 lines=50
```

## memory_search

Search memories semantically.

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| query | string | Yes | Search query |
| maxResults | int | No | Maximum results (default: 5) |
| minScore | float | No | Minimum similarity score |

## memory_get

Read memory file content.

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| path | string | No | File path (default: MEMORY.md) |
| from | int | No | Starting line (1-indexed) |
| lines | int | No | Number of lines |

## Examples

Search for project decisions:
```
memory_search query="project decisions" maxResults=10
```

Search for recent work:
```
memory_search query="what was I working on yesterday"
```

Read MEMORY.md:
```
memory_get path="MEMORY.md"
```

Read specific section:
```
memory_get path="memory/2026-02-07.md" from=1 lines=100
```

Search with minimum score:
```
memory_search query="git commands" minScore=0.7 maxResults=5
```
