#!/usr/bin/env python3
"""Initialize a new skill with template structure.

Usage:
    python3 scripts/init_skill.py <skill-name> --path skills/ [--resources scripts,references,assets]

Examples:
    python3 scripts/init_skill.py pdf-editor --path skills/
    python3 scripts/init_skill.py my-skill --path skills/ --resources scripts,references
"""

import argparse
import os
import sys
from pathlib import Path


def normalize_skill_name(name: str) -> str:
    """Normalize skill name to hyphen-case."""
    # Convert to lowercase
    name = name.lower()
    # Replace spaces with hyphens
    name = name.replace(" ", "-")
    # Keep only lowercase letters, digits, and hyphens
    result = "".join(c if c.isalnum() or c == "-" else "-" for c in name)
    # Remove multiple hyphens
    while "--" in result:
        result = result.replace("--", "-")
    return result


def create_skill_template(name: str, output_path: Path, resources: list) -> Path:
    """Create skill directory and template files.
    
    Args:
        name: Skill name (will be normalized)
        output_path: Parent directory for skill
        resources: List of resource directories to create
        
    Returns:
        Path to created skill directory
    """
    # Normalize name
    skill_name = normalize_skill_name(name)
    
    # Create skill directory
    skill_dir = output_path / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    # Create SKILL.md template
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f'''---
name: {skill_name}
description: A custom skill for {name}
metadata:
  emoji: 🎯
  requires:
    bins: []
    anyBins: []
    env: []
    config: []
---

# {skill_name.replace("-", " ").title()}

Brief description of what this skill does.

## Quick Start

\`\`\`
{skill_name} command="help"
\`\`\`

## Commands

| Command | Description |
|---------|-------------|
| help | Show this help message |

## Examples

Example usage patterns.

## See Also

- Related skills or documentation
''')
    
    # Create resource directories
    for resource in resources:
        resource_dir = skill_dir / resource
        resource_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {resource_dir}")
    
    return skill_dir


def main():
    parser = argparse.ArgumentParser(
        description="Initialize a new skill with template structure"
    )
    parser.add_argument(
        "name",
        help="Name of the skill (will be normalized to hyphen-case)"
    )
    parser.add_argument(
        "--path",
        default="skills/",
        help="Output path for skill directory (default: skills/)"
    )
    parser.add_argument(
        "--resources",
        default="scripts,references,assets",
        help="Comma-separated list of resources to create (default: scripts,references,assets)"
    )
    
    args = parser.parse_args()
    
    # Parse resources
    resources = [r.strip() for r in args.resources.split(",") if r.strip()]
    
    # Validate resources
    valid_resources = {"scripts", "references", "assets"}
    for resource in resources:
        if resource not in valid_resources:
            print(f"Error: Invalid resource '{resource}'. Valid: {valid_resources}")
            sys.exit(1)
    
    # Create output path
    output_path = Path(args.path).expanduser().resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    
    print(f"Initializing skill: {args.name}")
    print(f"Output path: {output_path}")
    print(f"Resources: {resources}")
    
    # Create skill
    skill_dir = create_skill_template(args.name, output_path, resources)
    
    print(f"\n✅ Skill created: {skill_dir}")
    print(f"\nNext steps:")
    print(f"  1. Edit {skill_dir / 'SKILL.md'}")
    print(f"  2. Add scripts to {skill_dir / 'scripts/'}")
    print(f"  3. Package: python3 scripts/package_skill.py {skill_dir}")


if __name__ == "__main__":
    main()
