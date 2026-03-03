#!/usr/bin/env python3
"""Add file reference handling to webchat.py"""

with open('src/gateway/webchat.py', 'r') as f:
    content = f.read()

old = '''        # Get model from config
        model = config.llm.get('model', 'gpt-5-mini')
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore(model=model)'''

new = '''        # Get model from config
        model = config.llm.get('model', 'gpt-5-mini')
        
        # Parse file references (@file_xxx or @filename)
        attached_images = []
        try:
            import re
            ref_pattern = r'@(file_[a-zA-Z0-9]+|[a-zA-Z0-9_]+)'
            refs = re.findall(ref_pattern, message)
            
            if refs:
                from src.utils.file_parser.storage import _file_metadata
                for ref_id in set(refs):
                    # Check if it's a file_ reference
                    if ref_id.startswith('file_'):
                        ref_id = ref_id[5:]
                    
                    for f in _file_metadata.values():
                        original_name = getattr(f, 'original_filename', '')
                        # Match by ID prefix or by filename
                        if f.file_id.startswith(ref_id) or (original_name and original_name.startswith(ref_id)):
                            if f.content_type.startswith('image/'):
                                import base64
                                file_path = f'/root/.efp/workspace/uploads/{f.stored_filename}'
                                try:
                                    with open(file_path, 'rb') as img:
                                        img_data = base64.b64encode(img.read()).decode('utf-8')
                                        ext = f.content_type.split('/')[-1]
                                        attached_images.append(f'data:image/{ext};base64,{img_data}')
                                    logger.info(f"[api_chat] Loaded image: {f.file_id}")
                                except Exception as e:
                                    logger.error(f"[api_chat] Failed to load image: {e}")
                            # Remove reference from message
                            message = re.sub(r'@' + re.escape(ref_id), '', message)
                            message = re.sub(r'@file_' + re.escape(ref_id[:8]), '', message)
                            break
        except Exception as e:
            logger.error(f"[api_chat] File reference error: {e}")
        
        # Run agent (history is managed internally by session_manager)
        agent = AgentCore(model=model)'''

content = content.replace(old, new)

with open('src/gateway/webchat.py', 'w') as f:
    f.write(content)

print('Done')
