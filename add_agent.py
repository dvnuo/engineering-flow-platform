with open('/root/engineering-flow-platform/src/agents/core.py', 'r') as f:
    content = f.read()

# Add attached_images parameter
old = '''    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:'''

new = '''    async def process(
        self,
        message: str,
        session_id: str,
        user_name: Optional[str] = None,
        track_usage: bool = True,
        reasoning_replay: Optional[bool] = None,
        stream_callback: Optional[Callable[[str], None]] = None,
        attached_images: Optional[List[str]] = None,
    ) -> Dict[str, Any]:'''

content = content.replace(old, new)

# Add image injection logic before the tool loop
old2 = '''        while iteration < max_tool_iterations:'''

new2 = '''        # ===== INJECT ATTACHED IMAGES =====
        if attached_images and len(messages) > 0:
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    user_content = messages[i].get("content", "")
                    msg_content = [{"type": "text", "text": user_content}]
                    for img in attached_images[:1]:
                        msg_content.append({"type": "image_url", "image_url": {"url": img}})
                    messages[i] = {"role": "user", "content": msg_content}
                    logger.info(f"[Agent] Attached {len(attached_images)} image(s) to user message")
                    break
        # ===== END IMAGE INJECTION =====

        while iteration < max_tool_iterations:'''

content = content.replace(old2, new2)

with open('/root/engineering-flow-platform/src/agents/core.py', 'w') as f:
    f.write(content)

print('Done')
