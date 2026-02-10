"""Fix persistence.py imports"""
import re

with open("src/sessions/persistence.py", "r") as f:
    content = f.read()

# Fix the broken import section
old_pattern = r'"""Session persistence layer for Engineering Flow Platform\.\nimport logging\n\nManages JSONL transcript files and sessions\.json store with TTL support\."""'
new_content = '''"""Session persistence layer for Engineering Flow Platform.

Manages JSONL transcript files and sessions.json store with TTL support.
"""

import asyncio
import json
import logging
import os'''

content = re.sub(old_pattern, new_content, content)

with open("src/sessions/persistence.py", "w") as f:
    f.write(content)

print("Fixed imports")
