"""Sub-agent Session Tools Schemas for LLM."""

SUBAGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "sessions_list",
            "description": "List active sessions including main session and sub-agents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "active_minutes": {
                        "type": "integer",
                        "description": "Filter sessions active within this many minutes",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to return",
                    },
                    "message_limit": {
                        "type": "integer",
                        "description": "Include up to N messages per session",
                    },
                    "kinds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by session kinds (e.g., ['direct', 'group'])",
                    },
                },
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sessions_history",
            "description": "Get message history for a specific session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_key": {
                        "type": "string",
                        "description": "Session identifier",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum messages to return (default: 50)",
                    },
                    "include_tools": {
                        "type": "boolean",
                        "description": "Include tool results in history",
                    },
                },
                "required": ["session_key"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sessions_send",
            "description": "Send a message to another session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_key": {
                        "type": "string",
                        "description": "Target session identifier",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message to send",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout for response (default: 60)",
                    },
                },
                "required": ["session_key", "message"],
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sessions_spawn",
            "description": "Spawn a sub-agent session to handle a task independently. The sub-agent will process the task and return results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Task description for the sub-agent",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to use (reserved for future use)",
                    },
                    "model": {
                        "type": "string",
                        "description": "Model to use for the sub-agent",
                    },
                    "thinking": {
                        "type": "string",
                        "description": "Thinking level: off, minimal, low, medium, high",
                    },
                    "cleanup": {
                        "type": "string",
                        "enum": ["delete", "keep"],
                        "description": "What to do with session after completion",
                    },
                    "label": {
                        "type": "string",
                        "description": "Human-readable label for the session",
                    },
                    "run_timeout_seconds": {
                        "type": "integer",
                        "description": "Maximum runtime in seconds",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout for the entire operation (default: 300)",
                    },
                },
                "required": ["task"],
            }
        }
    },
]
