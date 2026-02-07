# Web Search Tool - Web Search & Fetch

Search the web and fetch readable content from URLs.

## Usage

```bash
web_search query="Python async await tutorial" count=10 freshness="pw"
web_search query="2024 tech trends" country="US" searchLang="en"
web_fetch url="https://example.com/article" extractMode="markdown"
web_fetch url="https://example.com" maxChars=5000
```

## web_search

Search the web using Brave Search API.

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| query | string | Yes | Search query |
| count | int | No | Results count (1-10, default: 10) |
| freshness | string | No | pd (24h), pw (week), pm (month), py (year) |
| country | string | No | Country code (default: US) |
| searchLang | string | No | ISO language code |
| uiLang | string | No | UI language code |

### Freshness Options

| Value | Description |
|-------|-------------|
| pd | Past 24 hours |
| pw | Past week |
| pm | Past month |
| py | Past year |
| YYYY-MM-DDtoYYYY-MM-DD | Date range |

## web_fetch

Fetch and extract readable content from a URL.

| Parameter | Type | Required | Description |
|-----------|------|----------|------------|
| url | string | Yes | HTTP/HTTPS URL |
| extractMode | string | No | markdown or text (default: markdown) |
| maxChars | int | No | Maximum characters |

## Examples

Search for recent news:
```
web_search query="Python 3.12 release" freshness="pd" count=5
```

Search in different language:
```
web_search query="教程" country="CN" searchLang="zh"
```

Fetch article:
```
web_fetch url="https://example.com/article" extractMode="markdown"
```

Fetch with limit:
```
web_fetch url="https://example.com" maxChars=10000
```
