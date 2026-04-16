import pytest


@pytest.mark.asyncio
async def test_parse_adf_body_keeps_mention_nodes():
    from src.jira.api import JiraChannel

    channel = JiraChannel()
    try:
        body = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "ping "},
                        {"type": "mention", "attrs": {"displayName": "agent-user"}},
                    ],
                }
            ],
        }
        parsed = channel._parse_body(body)
        assert "@agent-user" in parsed
    finally:
        await channel.close()
