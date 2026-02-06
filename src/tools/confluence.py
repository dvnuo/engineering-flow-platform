"""
Confluence Tools - Agent 调用入口。

调用 src/integrations/confluence/api.py
"""

from src.integrations.confluence import ConfluenceChannel

# 全局实例
confluence_client = ConfluenceChannel()

# ========== 工具函数 ==========

async def confluence_get_page(space: str, title: str) -> str:
    """Get a Confluence page by space and title."""
    try:
        page = await confluence_client.get_page(space, title)
        if not page:
            return f"No page found: {space}:{title}"
        content = page.get("body", {}).get("storage", {}).get("value", "")[:500]
        return f"**{page.get('title', 'No title')}**\n\n{content}..."
    except Exception as e:
        return f"Error getting page: {e}"


async def confluence_search(query: str, max_results: int = 10) -> str:
    """Search Confluence pages using CQL."""
    try:
        result = await confluence_client.search(query, max_results)
        results = result.get("results", [])
        if not results:
            return "No pages found."
        lines = [f"**Search Results** ({len(results)}):\n"]
        for r in results:
            title = r.get("title", "No title")[:40]
            space = r.get("space", {}).get("key", "UNKNOWN")
            lines.append(f"- **{space}**: {title}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching: {e}"


def get_tools_schemas() -> list:
    """Return Confluence tool schemas for OpenAI."""
    return [
        {
            "type": "function",
            "function": {
                "name": "confluence_get_page",
                "description": "Get a Confluence page by space and title",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "space": {"type": "string", "description": "Space key"},
                        "title": {"type": "string", "description": "Page title"}
                    },
                    "required": ["space", "title"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "confluence_search",
                "description": "Search Confluence pages using CQL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "CQL query"},
                        "max_results": {"type": "integer", "description": "Max results", "default": 10}
                    },
                    "required": ["query"]
                }
            }
        },
    ]
