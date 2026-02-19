# confluence/ - Confluence Integration

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

## Usage

```python
from src.confluence import ConfluenceClient

cf = ConfluenceClient()
pages = cf.get_page(space_key, page_id)
```
