# Docs Directory

## Directory Structure

```
docs/
├── README.md                  # This file
├── README-template.md        # Template for new READMEs
├── (documentation files)
├── guides/                   # How-to guides
│   ├── getting-started.md   # Quick start guide
│   ├── installation.md      # Installation guide
│   ├── configuration.md     # Configuration guide
│   ├── deployment.md        # Deployment guide
│   └── troubleshooting.md   # Common issues and solutions
├── api/                      # API documentation
│   ├── agent-api.md        # Agent API reference
│   ├── channel-api.md      # Channel API reference
│   ├── skill-api.md        # Skill API reference
│   └── gateway-api.md       # Gateway API reference
├── architecture/             # Architecture documentation
│   ├── overview.md         # System overview
│   ├── components.md       # Component descriptions
│   ├── data-flow.md        # Data flow diagrams
│   └── security.md         # Security considerations
├── best-practices/          # Best practices
│   ├── coding-style.md     # Code style guide
│   ├── testing.md         # Testing guidelines
│   ├── documentation.md   # Documentation standards
│   └── deployment.md      # Deployment best practices
├── contributing/            # Contribution guidelines
│   ├── contributing.md    # How to contribute
│   ├── code-review.md     # Code review process
│   └── commit-message.md  # Commit message format
└── (additional documentation)
```

## Documentation Types

### 1. API Documentation
```markdown
# API Reference

## Endpoint: `/api/v1/messages`

### Description
Send a message to a channel.

### Request
```json
{
    "content": "Hello, world!",
    "channel": "discord",
    "reply_to": "msg-123"
}
```

### Response
```json
{
    "status": "success",
    "message_id": "msg-456"
}
```

### Errors
| Code | Description |
|------|-------------|
| 400 | Invalid request |
| 401 | Unauthorized |
| 429 | Rate limited |
```

### 2. Configuration Documentation
```markdown
# Configuration Guide

## Agent Configuration

### `agent.name`
- **Type**: `string`
- **Default**: `"engineering-flow-platform"`
- **Description**: The name of the agent.

### `agent.default_model`
- **Type**: `string`
- **Default**: `"gpt-4"`
- **Description**: Default LLM model to use.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `ANTHROPIC_API_KEY` | No | Anthropic API key |
```

### 3. Architecture Documentation
```markdown
# System Architecture

## Overview

Engineering Flow Platform is a multi-channel AI assistant framework...

## Components

### Agent Core
- Handles message processing
- Routes to appropriate skills
- Manages context and memory

### Channel Adapters
- Discord
- WhatsApp
- Telegram
- Slack
```

### 4. Guide Documentation
```markdown
# Getting Started

## Prerequisites
- Python 3.9+
- pip
- git

## Installation

```bash
git clone https://github.com/engineering-flow-platform/engineering-flow-platform.git
cd engineering-flow-platform
pip install -r requirements.txt
```

## Quick Start

```python
from engineering-flow-platform import Agent

agent = Agent()
agent.run()
```
```

## What Problems It Solves

- **Onboarding**: New developers can quickly understand the project
- **Reference**: Easy lookup for API and configuration
- **Standards**: Consistent documentation across the project
- **Knowledge Sharing**: Capture architectural decisions
- **Troubleshooting**: Quick solutions to common issues

## Configuration Options

### Documentation Settings (config.yaml)

```yaml
# docs/config.yaml

# Generation settings
docs:
  # Output directory
  output_dir: "docs/build"
  
  # Source directories
  source_dirs:
    - "docs/"
    - "README.md"
  
  # Excluded patterns
  exclude:
    - "**/README-template.md"
    - "**/.git/**"
  
  # Format settings
  format: "markdown"
  numbering: true
  toc_depth: 3
  
  # API documentation
  api:
    enabled: true
    source: "engineering-flow-platform/"
    output: "docs/api/"
    style: "google"    # google, numpy, sphinx
```

### Theme and Styling

```yaml
# docs/theme.yaml

theme:
  name: "material"
  palette:
    primary: "indigo"
    accent: "blue"
  features:
    navigation: true
    search: true
    code_copy: true
  font:
    family: "Roboto"
    size: "16px"
```

## How to Run

### Generate Documentation
```bash
# Using mkdocs
mkdocs build

# Using sphinx
sphinx-build -b html docs/ docs/build/

# Using pdoc
pdoc --output-dir docs/api/ engineering-flow-platform/
```

### Serve Documentation Locally
```bash
# MkDocs live reload
mkdocs serve

# Sphinx
sphinx-autobuild docs/ docs/build/

# pdoc
pdoc --serve engineering-flow-platform
```

### Build All Documentation
```bash
# Build API docs
pdoc --output-dir docs/api/ engineering-flow-platform/

# Build guides
mkdocs build

# Generate configuration reference
python scripts/gen_config_docs.py

# Build all
make docs
```

## Documentation Standards

### 1. File Naming
```
lowercase-with-hyphens.md
- getting-started.md
- configuration-guide.md
```

### 2. Header Format
```markdown
# Title (H1)

## Section (H2)

### Subsection (H3)

#### Deep Subsection (H4)
```

### 3. Code Blocks
```markdown
```python
def example():
    pass
```

```bash
# Terminal commands
pip install package
```

```yaml
# Configuration
key: value
```
```

### 4. Links
```markdown
[Internal Link](path/to/file.md)

[External Link](https://example.com)

[Anchor](#section-title)
```

### 5. Tables
```markdown
| Column 1 | Column 2 | Column 3 |
|----------|----------|----------|
| Data 1   | Data 2   | Data 3   |
| Data 4   | Data 5   | Data 6   |
```

### 6. Lists
```markdown
1. Numbered item
2. Numbered item
   - Nested item
   - Nested item

- Bullet item
- Bullet item
```

## Documentation Sections

### Required Sections for Each Document

| Section | Description |
|---------|-------------|
| Title | Clear, descriptive title |
| Overview | What this document covers |
| Prerequisites | What readers need before starting |
| Steps | Numbered instructions |
| Examples | Code and configuration examples |
| Reference | Links to related documentation |
| Troubleshooting | Common issues and solutions |

## Best Practices

### 1. Keep it Updated
```markdown
<!-- Document last updated: 2024-01-01 -->
<!-- Update this when making changes -->
```

### 2. Use Clear Language
```markdown
<!-- Good -->
Click the button to save your changes.

<!-- Avoid -->
After performing the action of clicking on the UI element...
```

### 3. Include Examples
```markdown
<!-- Good -->
```yaml
config:
  key: value
```

<!-- Bad -->
Set the configuration option.
```

### 4. Add Visual Aids
```markdown
![Diagram Description](path/to/diagram.png)

> **Note**: Important information
```

### 5. Cross-Reference
```markdown
See [Configuration Guide](configuration.md) for details.

For more information, refer to [API Reference](api/agent-api.md).
```

## File Formats

### Markdown (.md)
- Primary documentation format
- GitHub/GitLab compatible
- Easy to write and edit

### reStructuredText (.rst)
- Sphinx documentation
- Python project standard
- Advanced features

### YAML (.yaml)
- Configuration documentation
- Structure examples

### JSON (.json)
- API response examples
- Data structure documentation

## Documentation Tools

### MkDocs
```yaml
# mkdocs.yml
site_name: Engineering Flow Platform Documentation
nav:
  - Home: index.md
  - Guides:
    - getting-started.md
    - configuration.md
  - API:
    - api/agent-api.md
theme:
  name: material
```

### Sphinx
```rst
# conf.py
project = 'Engineering Flow Platform'
extensions = ['sphinx.ext.autodoc']
html_theme = 'alabaster'
```

## Contribution Guidelines

### Adding New Documentation

1. Choose appropriate directory:
   - `guides/` - How-to documents
   - `api/` - API references
   - `architecture/` - Design docs
   - `best-practices/` - Standards

2. Follow naming conventions
   - lowercase-with-hyphens.md
   - Descriptive filenames

3. Use template if available:
   ```bash
   cp docs/README-template.md docs/new-doc.md
   ```

4. Add to navigation:
   - Update `mkdocs.yml` for MkDocs
   - Update `index.rst` for Sphinx

### Updating Documentation

1. Make changes
2. Verify locally
3. Commit with descriptive message
4. Submit PR

### Documentation Review

- Check for accuracy
- Verify code examples
- Ensure clarity
- Check links

## Troubleshooting

### Links Broken
```bash
# Check all links
mkdocs build --strict

# Or use link checker
pip install mkdocs-linkcheck
mkdocs build
```

### Images Not Loading
```markdown
<!-- Use relative paths -->
![Image](images/diagram.png)

<!-- Not absolute paths -->
![Image](/home/user/project/images/diagram.png)
```

### Code Examples Outdated
```bash
# Run tests on code examples
pytest --doctest-modules engineering-flow-platform/
```

### Build Failures
```bash
# Check syntax
markdownlint README.md

# Check configuration
mkdocs --verbose build
```
