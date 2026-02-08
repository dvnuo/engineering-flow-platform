"""
Tool definitions and implementations for Jira and Confluence integration.

This module maintains backward compatibility by re-exporting from src/tools/*.
"""

# Import from new location for backward compatibility
from src.jira import get_tools_schemas as get_jira_tools_schemas
from src.confluence import get_tools_schemas as get_confluence_tools_schemas

# Jira Tools (backward compatible)
JIRA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "jira_get_issue",
            "description": "Get details for a Jira issue by key. Returns status, assignee, description, and other fields.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    }
                },
                "required": ["issue_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_search",
            "description": "Search Jira issues using JQL (Jira Query Language). Returns matching issues with key, summary, and status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "jql": {
                        "type": "string",
                        "description": "JQL query (e.g., 'project = PROJ AND status = Open')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
                    }
                },
                "required": ["jql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_add_comment",
            "description": "Add a comment to a Jira issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment text to add"
                    }
                },
                "required": ["issue_key", "comment"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_create_issue",
            "description": "Create a new Jira issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project key (e.g., 'PROJ')"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Issue summary/title"
                    },
                    "description": {
                        "type": "string",
                        "description": "Issue description"
                    },
                    "issue_type": {
                        "type": "string",
                        "description": "Issue type (Task, Bug, Story, etc.)",
                        "enum": ["Task", "Bug", "Story", "Epic"],
                        "default": "Task"
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority",
                        "enum": ["Highest", "High", "Medium", "Low", "Lowest"],
                        "default": "Medium"
                    }
                },
                "required": ["project", "summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_transition",
            "description": "Transition a Jira issue to a new status (e.g., 'In Progress', 'Done').",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    },
                    "to_status": {
                        "type": "string",
                        "description": "Target status name"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Optional comment for the transition"
                    }
                },
                "required": ["issue_key", "to_status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get_transitions",
            "description": "Get available status transitions for a Jira issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    }
                },
                "required": ["issue_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get_comments",
            "description": "Get comments for a Jira issue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    }
                },
                "required": ["issue_key"]
            }
        }
    },
]

# Confluence Tools
CONFLUENCE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "confluence_get_page",
            "description": "Get a Confluence page by space and title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space": {
                        "type": "string",
                        "description": "Space key (e.g., 'DEV')"
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title"
                    }
                },
                "required": ["space", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_search",
            "description": "Search Confluence pages using CQL (Confluence Query Language).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "CQL query (e.g., 'space = DEV AND title ~ \"API\"')"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        }
    },
]

# Combined tools for convenience
ALL_INTEGRATION_TOOLS = JIRA_TOOLS + CONFLUENCE_TOOLS

__all__ = [
    "JIRA_TOOLS",
    "CONFLUENCE_TOOLS", 
    "ALL_INTEGRATION_TOOLS",
]
