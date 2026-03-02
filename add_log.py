with open('/root/engineering-flow-platform/src/gateway/webchat.py', 'r') as f:
    content = f.read()

old = """        # Run agent (history is managed internally by session_manager)
        agent = AgentCore(model=model)"""

new = """        logger.info(f"[api_chat] Message after ref removal: '{message}', images: {len(attached_images)}")
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore(model=model)"""

content = content.replace(old, new)

with open('/root/engineering-flow-platform/src/gateway/webchat.py', 'w') as f:
    f.write(content)

print('Done')
