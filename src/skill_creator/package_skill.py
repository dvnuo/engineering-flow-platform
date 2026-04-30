#!/usr/bin/env python3
"""Package a skill into a distributable .skill file.

Usage:
    python3 -m src.skill_creator.package_skill <skill-path>
    python3 -m src.skill_creator.package_skill <skill-path> --output ./dist

The packaging script validates the skill first:
- Checks YAML frontmatter format
- Validates skill naming conventions
- Verifies required fields
- Ensures proper directory structure

If validation fails, errors are reported and no package is created.
"""

import argparse
import sys
import zipfile
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.truncate import truncate

from ruamel.yaml import YAML

# Module-level YAML instance
_yaml = YAML()


def validate_skill(skill_path: Path) -> tuple[bool, list]:
    """Validate skill structure and content.
    
    Args:
        skill_path: Path to skill directory
        
    Returns:
        Tuple of (is_valid, error_list)
    """
    errors = []
    warnings = []
    
    # Check skill directory exists
    if not skill_path.exists():
        return False, [f"Skill directory does not exist: {skill_path}"]
    
    if not skill_path.is_dir():
        return False, [f"Path is not a directory: {skill_path}"]
    
    # Check for skill.md (canonical), with legacy fallback
    skill_md = skill_path / "skill.md"
    if not skill_md.exists():
        legacy_skill_md = skill_path / "SKILL.md"
        if legacy_skill_md.exists():
            warnings.append("Found legacy SKILL.md; please migrate to lowercase skill.md")
            skill_md = legacy_skill_md
        else:
            errors.append("skill.md is required")
            return False, errors

    # Read and parse skill file
    try:
        content = skill_md.read_text(encoding="utf-8")
    except Exception as e:
        errors.append(f"Failed to read {skill_md.name}: {e}")
        return False, errors
    
    # Parse YAML frontmatter
    frontmatter = {}
    body = ""
    in_frontmatter = False
    after_frontmatter = False
    
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not in_frontmatter and not after_frontmatter:
                in_frontmatter = True
            elif in_frontmatter:
                in_frontmatter = False
                after_frontmatter = True
                # Parse YAML
                yaml_content = "\n".join(lines[1:i])
                frontmatter = parse_frontmatter(yaml_content)
            continue
        
        if after_frontmatter:
            body = "\n".join(lines[i+1:])
            break
    
    # Validate frontmatter
    for required_field in ["name", "description", "version", "owner"]:
        if required_field not in frontmatter:
            errors.append(f"YAML frontmatter must include '{required_field}' field")

    triggers = frontmatter.get("triggers") or frontmatter.get("trigger")
    if not triggers:
        errors.append("YAML frontmatter must include 'triggers' or 'trigger' field")
    
    # Validate name
    name = frontmatter.get("name", "")
    if name:
        if not validate_skill_name(name):
            errors.append(f"Invalid skill name '{name}': must be lowercase with hyphens, max 64 chars")
    
    # Validate description
    description = frontmatter.get("description", "")
    if description:
        if len(description) < 10:
            warnings.append(f"Description seems short ({len(description)} chars): {truncate(description, 50)}")
    
    # Check optional directories
    for dir_name in ["scripts", "references", "assets"]:
        dir_path = skill_path / dir_name
        if dir_path.exists() and not any(dir_path.iterdir()):
            warnings.append(f"Directory '{dir_name}' is empty")
    
    return len(errors) == 0, errors + warnings


def parse_frontmatter(yaml_str: str) -> dict:
    """Parse YAML frontmatter string."""
    try:
        data = _yaml.load(yaml_str)
        return data if data else {}
    except Exception as e:
        return {}


def validate_skill_name(name: str) -> bool:
    """Validate skill name format.
    
    Rules:
    - Letters (including unicode), digits, hyphens only
    - Max 64 characters
    - No leading/trailing hyphens
    - No consecutive hyphens
    - Uppercase letters not allowed
    """
    if not name:
        return False
    
    if len(name) > 64:
        return False
    
    # Check first character
    first = name[0]
    if not (first.isdigit() or first.isalpha()):
        return False
    # Reject uppercase
    if first.isupper():
        return False
    
    # Check last character
    last = name[-1]
    if not (last.isdigit() or last.isalpha()):
        return False
    # Reject uppercase
    if last.isupper():
        return False
    
    # Check each character
    for char in name:
        if char == "-":
            continue
        if char.isdigit():
            continue
        # Allow unicode letters (Chinese, etc.) or lowercase letters
        if char.isalpha() and not char.isupper():
            continue
        # Reject uppercase letters
        return False
    
    # Check for consecutive hyphens
    if "--" in name:
        return False
    
    return True


def package_skill(skill_path: Path, output_path: Path = None) -> Path:
    """Package skill into a .skill zip file.
    
    Args:
        skill_path: Path to skill directory
        output_path: Output directory for .skill file
        
    Returns:
        Path to created .skill file
    """
    # Validate first
    is_valid, messages = validate_skill(skill_path)
    
    if not is_valid:
        print("❌ Validation failed:")
        for msg in messages:
            print(f"  - {msg}")
        sys.exit(1)
    
    if messages:
        print("⚠️  Validation warnings:")
        for msg in messages:
            print(f"  - {msg}")
    
    # Determine output path
    if output_path is None:
        output_path = skill_path.parent
    
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Get skill name from path
    skill_name = skill_path.name
    
    # Create output filename
    output_file = output_path / f"{skill_name}.skill"
    
    # Create zip file
    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in skill_path.rglob("*"):
            if file_path.is_file():
                # Calculate relative path within skill
                rel_path = file_path.relative_to(skill_path)
                zf.write(file_path, arcname=str(rel_path))
                print(f"  Added: {rel_path}")
    
    print(f"\n✅ Skill packaged: {output_file}")
    print(f"  Size: {output_file.stat().st_size / 1024:.1f} KB")
    
    return output_file


def main():
    parser = argparse.ArgumentParser(
        description="Package a skill into a distributable .skill file"
    )
    parser.add_argument(
        "skill_path",
        help="Path to skill directory"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output directory for .skill file (default: same as skill directory)"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate, don't package"
    )
    
    args = parser.parse_args()
    
    skill_path = Path(args.skill_path).expanduser().resolve()
    
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = skill_path.parent
    
    print(f"Packaging skill: {skill_path}")
    print(f"Output: {output_path}")
    
    # Validate
    is_valid, messages = validate_skill(skill_path)
    
    if not is_valid:
        print("\n❌ Validation failed:")
        for msg in messages:
            print(f"  - {msg}")
        sys.exit(1)
    
    print("\n✅ Validation passed")
    if messages:
        for msg in messages:
            print(f"  ⚠️  {msg}")
    
    if not args.validate_only:
        package_skill(skill_path, output_path)


if __name__ == "__main__":
    main()
