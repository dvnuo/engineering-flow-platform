# confluence/ - Confluence Integration

## Overview

The Confluence module provides integration with Atlassian Confluence for documentation, knowledge management, and team collaboration spaces.

## Structure

```
confluence/
├── api.py      # Confluence REST API client
└── __init__.py # Module exports
```

## Components

### Confluence API (`api.py`)
- Page operations (create, update, retrieve)
- Space management
- Content search
- Attachment handling
- Labels and macros

## Quick Start

```python
from src.confluence import ConfluenceClient

# Initialize with credentials from config
cf = ConfluenceClient()

# Get a page
page = cf.get_page(space_key="SPACE", page_id=123456)

# Create a new page
new_page = cf.create_page(
    space_key="SPACE",
    title="New Page Title",
    content="<h1>Page Content</h1><p>Description</p>",
    parent_id=None  # None for top-level page
)

# Update existing page
cf.update_page(page_id=123456, title="Updated Title", content="New content")
```

## Configuration

```yaml
# In config.yaml
confluence:
  base_url: "https://your-domain.atlassian.net/wiki"
  email: "your-email@example.com"
  api_token: "your-api-token"
  default_space: "SPACE"
```

## Dependencies

- `requests` - HTTP library for REST API calls
- Standard library: `json`, `logging`

## Development Guide

### Supported Operations

| Operation | Method | Description |
|-----------|--------|-------------|
| Get Page | `get_page(space, page_id)` | Retrieve page content |
| Create Page | `create_page(**params)` | Create new page |
| Update Page | `update_page(page_id, ...)` | Modify page content |
| Delete Page | `delete_page(page_id)` | Remove page |
| Search | `search_content(query)` | Search confluence |
| Get Space | `get_space(space_key)` | Get space info |

### Content Format

Confluence uses storage format (XML-based):

```python
content = """
<ac:structured-macro ac:name="info">
  <ac:rich-text-body>
    <p>Information block</p>
  </ac:rich-text-body>
</ac:structured-macro>
"""
```

### Macro Support

| Macro | Description |
|-------|-------------|
| info | Information notice |
| warning | Warning notice |
| tip | Tip box |
| note | Note box |

### Best Practices

- Use macros for consistent formatting
- Label pages for better discoverability
- Use parent pages for hierarchy
- Handle rate limits gracefully
