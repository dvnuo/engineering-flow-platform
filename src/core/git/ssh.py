"""
Git Tool Definitions for AI integration.

Register git tools with the integration system.
"""

# Git Tools Schema
GIT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show working tree status. Returns clean/dirty status with staged/unstaged files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional, defaults to workspace)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes and commit with a message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message"
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_push",
            "description": "Push to remote branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name (default: main)",
                        "default": "main"
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_pull",
            "description": "Pull from remote repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "List branches or create/delete a branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Branch name (for create/delete)"
                    },
                    "delete": {
                        "type": "boolean",
                        "description": "Delete the branch",
                        "default": False
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent commit history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of commits to show",
                        "default": 10
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_checkout",
            "description": "Switch to a different branch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "branch": {
                        "type": "string",
                        "description": "Branch name to switch to"
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                },
                "required": ["branch"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show unstaged changes in the working directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_add",
            "description": "Stage files for commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path or '.' for all",
                        "default": "."
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Repository path (optional)"
                    }
                }
            }
        }
    },
]
