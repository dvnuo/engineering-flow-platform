"""
Tool definitions and implementations for Jira and Confluence integration.

These tools are used by the Agent to interact with Jira and Confluence.
"""

# Jira Tools
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
                        "default": "Task"
                    },
                    "priority": {
                        "type": "string",
                        "description": "Priority (High, Medium, Low, etc.)",
                        "enum": ["Highest", "High", "Medium", "Low", "Lowest"],
                        "default": "Medium"
                    }
                },
                "required": ["project", "summary", "description"]
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
                        "description": "Target status name (e.g., 'In Progress', 'Done')"
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
            "description": "Get all comments for a Jira issue. Returns comments with author, date, and content. Useful for understanding discussion history.",
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
            "name": "jira_edit_issue",
            "description": "Edit/Update an existing Jira issue. Allows modifying summary, description, priority, and labels. At least one field to update is required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key to edit (e.g., 'PROJ-123')"
                    },
                    "summary": {
                        "type": "string",
                        "description": "New summary/title (optional)"
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (optional)"
                    },
                    "priority": {
                        "type": "string",
                        "description": "New priority - High, Medium, Low, etc. (optional, v3 only)"
                    },
                    "labels": {
                        "type": "string",
                        "description": "Comma-separated labels (optional, v3 only)"
                    }
                },
                "required": ["issue_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_assign_issue",
            "description": "Assign a Jira issue to a user. Use '-' to unassign.",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_key": {
                        "type": "string",
                        "description": "Issue key (e.g., 'PROJ-123')"
                    },
                    "assignee": {
                        "type": "string",
                        "description": "Account ID, email, or '-' to unassign"
                    }
                },
                "required": ["issue_key", "assignee"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get_my_issues",
            "description": "Get issues assigned to the current user. Optionally filter by status.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional status filter (e.g., 'Open', 'In Progress')"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "jira_get_project_issues",
            "description": "Get issues in a project. Defaults to configured project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project key (optional, defaults to configured project)"
                    },
                    "status": {
                        "type": "string",
                        "description": "Optional status filter"
                    }
                }
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
            "description": "Get a Confluence page by ID. Returns title, content, version, and metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID (numeric string)"
                    }
                },
                "required": ["page_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_search",
            "description": "Search Confluence pages using CQL (Confluence Query Language). Returns matching pages with title and ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cql": {
                        "type": "string",
                        "description": "CQL query (e.g., 'space = DEV AND text ~ \"API\"')"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results",
                        "default": 10
                    }
                },
                "required": ["cql"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_create_page",
            "description": "Create a new Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key (e.g., 'DEV')"
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title"
                    },
                    "content": {
                        "type": "string",
                        "description": "Page content (supports HTML/storage format)"
                    },
                    "parent_id": {
                        "type": "string",
                        "description": "Parent page ID for nested pages (optional)"
                    }
                },
                "required": ["space_key", "title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_update_page",
            "description": "Update an existing Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID to update"
                    },
                    "title": {
                        "type": "string",
                        "description": "New page title"
                    },
                    "content": {
                        "type": "string",
                        "description": "New page content"
                    }
                },
                "required": ["page_id", "title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_add_comment",
            "description": "Add a comment to a Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID"
                    },
                    "comment": {
                        "type": "string",
                        "description": "Comment text"
                    }
                },
                "required": ["page_id", "comment"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_list_spaces",
            "description": "List available Confluence spaces.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of spaces to return",
                        "default": 20
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_delete_page",
            "description": "Delete a Confluence page by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID to delete"
                    }
                },
                "required": ["page_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_get_page_by_title",
            "description": "Get a Confluence page by space and title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key (e.g., 'DEV')"
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title to find"
                    }
                },
                "required": ["space_key", "title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_get_space",
            "description": "Get details of a Confluence space.",
            "parameters": {
                "type": "object",
                "properties": {
                    "space_key": {
                        "type": "string",
                        "description": "Space key (e.g., 'DEV')"
                    }
                },
                "required": ["space_key"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_get_comments",
            "description": "Get all comments for a Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID"
                    }
                },
                "required": ["page_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_add_label",
            "description": "Add a label to a Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID"
                    },
                    "label": {
                        "type": "string",
                        "description": "Label to add"
                    }
                },
                "required": ["page_id", "label"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confluence_remove_label",
            "description": "Remove a label from a Confluence page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "Page ID"
                    },
                    "label": {
                        "type": "string",
                        "description": "Label to remove"
                    }
                },
                "required": ["page_id", "label"]
            }
        }
    },
]

# All integration tools
INTEGRATION_TOOLS = JIRA_TOOLS + CONFLUENCE_TOOLS


# ========== Tool Implementations ==========

async def jira_get_issue(issue_key: str) -> str:
    """Get details for a Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        issue = await jira_channel.get_issue(issue_key)
        fields = issue.get("fields", {})
        status = fields.get("status", {}).get("name", "Unknown")
        assignee = fields.get("assignee", {})
        assignee_name = assignee.get("displayName", "Unassigned") if assignee else "Unassigned"
        summary = fields.get("summary", "")
        description = _parse_adf_body(fields.get("description", ""))
        return f"""**{issue_key}: {summary}**

**Status:** {status}
**Assignee:** {assignee_name}
**Priority:** {fields.get("priority", {}).get("name", "None")}
**Type:** {fields.get("issuetype", {}).get("name", "Task")}

**Description:**
{description[:500]}{'...' if len(description) > 500 else ''}"""
    except Exception as e:
        return f"Error getting issue {issue_key}: {str(e)}"


async def jira_search(jql: str, max_results: int = 10) -> str:
    """Search Jira issues using JQL."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        result = await jira_channel.search_issues(jql, max_results=max_results)
        issues = result.get("issues", [])
        total = result.get("total", 0)
        if not issues:
            return f"No issues found for JQL: {jql}"
        lines = [f"**Search Results** ({total} total, showing {len(issues)}):\n"]
        for issue in issues:
            key = issue.get("key")
            status = issue.get("fields", {}).get("status", {}).get("name", "?")
            summary = issue.get("fields", {}).get("summary", "")[:50]
            lines.append(f"- **{key}** [{status}] {summary}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching issues: {str(e)}"


async def jira_add_comment(issue_key: str, comment: str) -> str:
    """Add a comment to a Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        result = await jira_channel.add_comment(issue_key, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added to {issue_key}: ID={comment_id}"
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def jira_create_issue(project: str, summary: str, description: str, 
                           issue_type: str = "Task", priority: str = "Medium") -> str:
    """Create a new Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        result = await jira_channel.create_issue(
            project=project or jira_channel.project,
            summary=summary,
            description=description,
            issue_type=issue_type,
            priority=priority
        )
        issue_key = result.get("key", "unknown")
        return f"Issue created: **{issue_key}**\nSummary: {summary[:50]}"
    except Exception as e:
        return f"Error creating issue: {str(e)}"


async def jira_transition(issue_key: str, to_status: str, comment: str = None) -> str:
    """Transition a Jira issue to a new status."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        transitions = await jira_channel.get_transitions(issue_key)
        transition_id = None
        for t in transitions:
            if t.get("name", "").lower() == to_status.lower():
                transition_id = t.get("id")
                break
        if not transition_id:
            available = [t.get("name") for t in transitions]
            return f"Cannot transition to '{to_status}'. Available: {', '.join(available)}"
        await jira_channel.transition_issue(issue_key, transition_id, comment)
        return f"{issue_key} transitioned to '{to_status}'"
    except Exception as e:
        return f"Error transitioning issue: {str(e)}"


async def jira_get_transitions(issue_key: str) -> str:
    """Get available transitions for a Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        transitions = await jira_channel.get_transitions(issue_key)
        if not transitions:
            return f"No transitions available for {issue_key}"
        lines = [f"**Available Transitions for {issue_key}:**\n"]
        for t in transitions:
            lines.append(f"- {t.get('name')} (ID: {t.get('id')})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting transitions: {str(e)}"


async def jira_get_comments(issue_key: str) -> str:
    """Get all comments for a Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    try:
        comments = await jira_channel.get_comments(issue_key)
        if not comments:
            return f"No comments found for {issue_key}"
        lines = [f"**Comments for {issue_key}** ({len(comments)} total):\n"]
        for i, comment in enumerate(comments, 1):
            author = comment.get("author", "Unknown")
            created = comment.get("created", "")[:10] if comment.get("created") else "N/A"
            body = comment.get("body", "")
            lines.append(f"---")
            lines.append(f"**Comment #{i}** by {author} on {created}")
            lines.append(f"{body}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting comments: {str(e)}"


async def jira_edit_issue(
    issue_key: str,
    summary: str = None,
    description: str = None,
    priority: str = None,
    labels: str = None
) -> str:
    """Edit/Update an existing Jira issue."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    # Check if at least one field is provided
    if not any([summary, description, priority, labels]):
        return "Error: At least one of summary, description, priority, or labels must be provided"
    
    # Parse labels if provided as comma-separated string
    labels_list = None
    if labels:
        labels_list = [l.strip() for l in labels.split(",") if l.strip()]
    
    try:
        success = await jira_channel.update_issue(
            issue_key=issue_key,
            summary=summary,
            description=description,
            priority=priority,
            labels=labels_list
        )
        
        if success:
            changes = []
            if summary:
                changes.append("summary")
            if description:
                changes.append("description")
            if priority:
                changes.append(f"priority to {priority}")
            if labels_list:
                changes.append(f"labels to {labels}")
            
            change_str = ", ".join(changes) if changes else "no changes"
            return f"Successfully updated {issue_key}: {change_str}"
        else:
            return f"Failed to update {issue_key}"
            
    except Exception as e:
        return f"Error editing issue: {str(e)}"


async def jira_assign_issue(issue_key: str, assignee: str) -> str:
    """Assign a Jira issue to a user. Use '-' to unassign."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        success = await jira_channel.assign_issue(issue_key, assignee)
        if success:
            if assignee == "-":
                return f"Successfully unassigned {issue_key}"
            return f"Successfully assigned {issue_key} to {assignee}"
        else:
            return f"Failed to assign {issue_key}"
    except Exception as e:
        return f"Error assigning issue: {str(e)}"


async def jira_get_my_issues(status: str = None) -> str:
    """Get issues assigned to the current user."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        issues = await jira_channel.get_my_issues(status)
        if not issues:
            return "No issues assigned to you" + (f" with status '{status}'" if status else "")
        
        lines = [f"**Your Issues** ({len(issues)} total):\n"]
        for issue in issues:
            key = issue.get("key")
            summary = issue.get("fields", {}).get("summary", "")[:50]
            status = issue.get("fields", {}).get("status", {}).get("name", "?")
            lines.append(f"- **{key}** [{status}] {summary}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting your issues: {str(e)}"


async def jira_get_project_issues(project: str = None, status: str = None) -> str:
    """Get issues in a project."""
    from channel.jira import jira_channel
    if not jira_channel.is_configured():
        return "Error: Jira not configured"
    
    try:
        issues = await jira_channel.get_project_issues(project, status)
        if not issues:
            proj = project or jira_channel.project
            return f"No issues found in project {proj}" + (f" with status '{status}'" if status else "")
        
        lines = [f"**Project Issues** ({len(issues)} total):\n"]
        for issue in issues:
            key = issue.get("key")
            summary = issue.get("fields", {}).get("summary", "")[:50]
            status = issue.get("fields", {}).get("status", {}).get("name", "?")
            lines.append(f"- **{key}** [{status}] {summary}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting project issues: {str(e)}"


async def confluence_get_page(page_id: str) -> str:
    """Get a Confluence page by ID."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        page = await confluence_channel.get_page(page_id)
        title = page.get("title", "Untitled")
        version = page.get("version", {}).get("number", "?")
        body = page.get("body", {}).get("storage", {}).get("value", "")[:500]
        return f"**{title}** (ID: {page_id}, Version: {version})\n\n{body}..."
    except Exception as e:
        return f"Error getting page {page_id}: {str(e)}"


async def confluence_search(cql: str, limit: int = 10) -> str:
    """Search Confluence pages using CQL."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        result = await confluence_channel.search_pages(cql, limit=limit)
        pages = result.get("results", [])
        total = result.get("size", 0)
        if not pages:
            return f"No pages found for CQL: {cql}"
        lines = [f"**Search Results** ({total} total, showing {len(pages)}):\n"]
        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id")
            lines.append(f"- **{title}** ({page_id})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error searching pages: {str(e)}"


async def confluence_create_page(space_key: str, title: str, content: str, parent_id: str = None) -> str:
    """Create a new Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        space = space_key or confluence_channel.space
        result = await confluence_channel.create_page(space, title, content, parent_id)
        page_id = result.get("id", "unknown")
        return f"Page created: **{title}** (ID: {page_id})"
    except Exception as e:
        return f"Error creating page: {str(e)}"


async def confluence_update_page(page_id: str, title: str, content: str) -> str:
    """Update a Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        result = await confluence_channel.update_page(page_id, title, content)
        return f"Page updated: **{title}** (ID: {page_id})"
    except Exception as e:
        return f"Error updating page: {str(e)}"


async def confluence_add_comment(page_id: str, comment: str) -> str:
    """Add a comment to a Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        result = await confluence_channel.add_comment(page_id, comment)
        comment_id = result.get("id", "unknown")
        return f"Comment added to page {page_id}: ID={comment_id}"
    except Exception as e:
        return f"Error adding comment: {str(e)}"


async def confluence_list_spaces(limit: int = 20) -> str:
    """List available Confluence spaces."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        spaces = await confluence_channel.list_spaces(limit)
        if not spaces:
            return "No spaces found"
        lines = [f"**Confluence Spaces** ({len(spaces)}):\n"]
        for space in spaces:
            key = space.get("key", "?")
            name = space.get("name", "Unknown")
            lines.append(f"- **{name}** (key: {key})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing spaces: {str(e)}"


async def confluence_delete_page(page_id: str) -> str:
    """Delete a Confluence page by ID."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        success = await confluence_channel.delete_page(page_id)
        if success:
            return f"Successfully deleted page {page_id}"
        else:
            return f"Failed to delete page {page_id}"
    except Exception as e:
        return f"Error deleting page: {str(e)}"


async def confluence_get_page_by_title(space_key: str, title: str) -> str:
    """Get a Confluence page by space and title."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        page = await confluence_channel.get_page_by_title(space_key, title)
        if page:
            page_id = page.get("id", "?")
            version = page.get("version", {}).get("number", "?")
            body = page.get("body", {}).get("storage", {}).get("value", "")[:300]
            return f"**{title}** (ID: {page_id}, Version: {version})\n\n{body}..."
        else:
            return f"Page '{title}' not found in space {space_key}"
    except Exception as e:
        return f"Error getting page: {str(e)}"


async def confluence_get_space(space_key: str) -> str:
    """Get details of a Confluence space."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        space = await confluence_channel.get_space(space_key)
        if space:
            name = space.get("name", "Unknown")
            key = space.get("key", "?")
            description = space.get("description", {}).get("plain", {}).get("value", "No description")[:200]
            return f"**{name}** (key: {key})\n\n{description}..."
        else:
            return f"Space {space_key} not found"
    except Exception as e:
        return f"Error getting space: {str(e)}"


async def confluence_get_comments(page_id: str) -> str:
    """Get all comments for a Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        comments = await confluence_channel.get_comments(page_id)
        if not comments:
            return f"No comments found for page {page_id}"
        lines = [f"**Comments for {page_id}** ({len(comments)} total):\n"]
        for i, comment in enumerate(comments, 1):
            author = comment.get("history", {}).get("createdBy", {}).get("displayName", "Unknown")
            created = comment.get("created", "")[:10] if comment.get("created") else "N/A"
            body = comment.get("body", {}).get("storage", {}).get("value", "")[:200]
            lines.append(f"---")
            lines.append(f"**Comment #{i}** by {author} on {created}")
            lines.append(f"{body}...")
        return "\n".join(lines)
    except Exception as e:
        return f"Error getting comments: {str(e)}"


async def confluence_add_label(page_id: str, label: str) -> str:
    """Add a label to a Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        success = await confluence_channel.add_label(page_id, label)
        if success:
            return f"Successfully added label '{label}' to page {page_id}"
        else:
            return f"Failed to add label '{label}' to page {page_id}"
    except Exception as e:
        return f"Error adding label: {str(e)}"


async def confluence_remove_label(page_id: str, label: str) -> str:
    """Remove a label from a Confluence page."""
    from channel.confluence import confluence_channel
    if not confluence_channel.is_configured():
        return "Error: Confluence not configured"
    try:
        success = await confluence_channel.remove_label(page_id, label)
        if success:
            return f"Successfully removed label '{label}' from page {page_id}"
        else:
            return f"Failed to remove label '{label}' from page {page_id}"
    except Exception as e:
        return f"Error removing label: {str(e)}"


def _parse_adf_body(body) -> str:
    """Extract text from Atlassian Document Format."""
    if isinstance(body, str):
        return body
    if not isinstance(body, dict):
        return ""
    content = body.get("content", [])
    if not content:
        return ""
    text_parts = []
    for block in content:
        _extract_text(block, text_parts)
    return "".join(text_parts)


def _extract_text(block, text_parts):
    """Recursively extract text from ADF block."""
    if not block:
        return
    block_type = block.get("type", "")
    if block_type == "text":
        text_parts.append(block.get("text", ""))
    elif block_type in ("paragraph", "heading"):
        for item in block.get("content", []):
            _extract_text(item, text_parts)
        text_parts.append("\n")
