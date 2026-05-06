import json


async def execute_tool_async(*, tools_dir, tool, args=None, context=None):
    return {
        "success": True,
        "content": json.dumps(
            {
                "tool": tool,
                "args": args or {},
                "runtime_type": (context or {}).get("runtime_type"),
                "session_id": (context or {}).get("session_id"),
                "source": "runtime_contract_fixture",
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    }
