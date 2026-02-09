#!/usr/bin/env python3
"""Fix duplicate workspace path in config.yaml.example"""
import re
from pathlib import Path

def fix_config():
    config_path = Path(__file__).parent.parent / "config.yaml.example"
    with open(config_path, "r") as f:
        content = f.read()
    
    if "workspace: \"~/.efp/workspace\"" in content:
        pattern = r"""(  # Cache settings
  cache:
    enabled: true  # Enable embedding cache
    max_entries: 50000  # Maximum cached embeddings

  # Storage paths
  path: "~/.efp/memory"  # SQLite database location
  workspace: "~/.efp/workspace"  # Workspace files)"""
        replacement = """  # Cache settings
  cache:
    enabled: true  # Enable embedding cache
    max_entries: 50000  # Maximum cached embeddings

  # SQLite database location
  path: "~/.efp/memory"  # SQLite database location"""
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        with open(config_path, "w") as f:
            f.write(content)
        print("Fixed duplicate workspace path")

if __name__ == "__main__":
    fix_config()

