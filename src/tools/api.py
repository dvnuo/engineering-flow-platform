"""Simple test tool for debugging tool calling in workflows."""

import json
from typing import List, Dict, Any


async def test_echo(message: str = "", data: str = "") -> str:
    """Echo back the input arguments for testing.
    
    Args:
        message: Message to echo back
        data: Additional data to include
        
    Returns:
        JSON string with echoed arguments
    """
    return json.dumps({
        "success": True,
        "echoed": {
            "message": message,
            "data": data,
        },
        "test": "test_echo tool works!"
    })


def get_tools_schemas() -> List[Dict]:
    """Get tool schemas for test_echo."""
    return [
        {
            "type": "function",
            "function": {
                "name": "test_echo",
                "description": "Echo back the input arguments. For testing tool execution in workflows.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to echo back"
                        },
                        "data": {
                            "type": "string",
                            "description": "Additional data to include"
                        }
                    },
                    "required": ["message"]
                }
            }
        }
    ]
