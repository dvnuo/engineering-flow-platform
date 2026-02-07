---
name: executor
description: Execute shell commands, read/write/edit files, search the web, and manage code repositories
metadata:
  emoji: 🛠️
  requires:
    bins: [python3, git]
    anyBins: []
    env: []
    config: []
---
# Executor Skill - Core Tool Execution

Execute shell commands, read/write/edit files, search the web, and manage code repositories.

## Skill Signature

The executor provides multiple tool functions:

### Shell Commands

\`\`\`python
exec(command: str, timeout: int = 30) -> ToolResult
\`\`\`

### File Operations

\`\`\`python
read(path: str, limit: int = 100, offset: int = 1) -> ToolResult
write(path: str, content: str) -> ToolResult
edit(path: str, oldText: str, newText: str) -> ToolResult
\`\`\`

### Web Search

\`\`\`python
web_search(query: str, count: int = 5) -> ToolResult
web_fetch(url: str, extractMode: str = "markdown", maxChars: int = 50000) -> ToolResult
\`\`\`

### Git Operations

\`\`\`python
git(command: str, args: str = "", timeout: int = 30) -> ToolResult
gh(command: str, args: str = "", timeout: int = 30) -> ToolResult
\`\`\`

## Examples

### Shell Commands

\`\`\`python
# Execute a shell command
exec(command="ls -la")

# Execute with timeout
exec(command="sleep 10", timeout=15)

# Run a Python script
exec(command="python3 my_script.py")
\`\`\`

### File Operations

\`\`\`python
# Read a file
read(path="/path/to/file.txt")

# Read specific lines
read(path="/path/to/file.txt", limit=50, offset=100)

# Write to a file
write(path="/path/to/new_file.txt", content="Hello, World!")

# Edit a file
edit(
    path="/path/to/file.txt",
    oldText="old content",
    newText="new content"
)
\`\`\`

### Web Search

\`\`\`python
# Search the web
web_search(query="Python async await tutorial", count=10)

# Fetch a URL
web_fetch(url="https://example.com/article")
\`\`\`

### Git Operations

\`\`\`python
# Git status
git(command="status")

# Git commit
git(command="commit", args="-m 'feat: add new feature'")

# GitHub CLI
gh(command="repo list", args="--limit 10")
\`\`\`

## Common Use Cases

### 1. Code Review

\`\`\`python
# Read a file
code = read(path="/path/to/code.py")

# Search for patterns
exec(command="grep -n 'TODO' /path/to/code.py")
\`\`\`

### 2. File Management

\`\`\`python
# Create a new file
write(path="/path/to/new_module.py", content="# New module")

# Update existing file
edit(path="/path/to/module.py", oldText="# Old", newText="# New")
\`\`\`

### 3. Web Research

\`\`\`python
# Search for information
results = web_search(query="latest Python 3.12 features", count=5)

# Fetch documentation
docs = web_fetch(url="https://docs.python.org/3/")
\`\`\`

## Timeout Handling

Commands have a default timeout of 30 seconds. For long-running tasks:

\`\`\`python
# Increase timeout for long operations
exec(command="make build", timeout=300)
\`\`\`

## Error Handling

\`\`\`python
# Check result
result = exec(command="ls /nonexistent")
if not result.success:
    print(f"Error: {result.error}")
\`\`\`
