"""Skill Creator Skill - Create and package AgentSkills."""

from skills.decorator import SkillResult


def skill_creator(
    command: str = "help",
    name: str = None,
    path: str = "skills/",
    resources: str = "scripts,references,assets",
    validate_only: bool = False,
) -> SkillResult:
    """Create, validate, or package skills.
    
    Args:
        command: Action to perform (help, init, package, validate, list)
        name: Name of the skill to create
        path: Output/input path for skill
        resources: Resources to create (scripts,references,assets)
        validate_only: Only validate without packaging
        
    Returns:
        SkillResult with output or error
    """
    import subprocess
    import sys
    from pathlib import Path
    
    script_dir = Path(__file__).parent / "scripts"
    init_script = script_dir / "init_skill.py"
    package_script = script_dir / "package_skill.py"
    
    if command == "help":
        output = """# Skill Creator Commands

## Available Commands

### init - Create a new skill
```
skill_creator command="init" name="my-skill" path="skills/"
```
Options:
- `name`: Skill name (required)
- `path`: Output directory (default: skills/)
- `resources`: Resources to create (default: scripts,references,assets)

### package - Package a skill
```
skill_creator command="package" path="skills/my-skill"
```
Options:
- `path`: Skill directory (required)
- `--output`: Output directory for .skill file

### validate - Validate a skill
```
skill_creator command="validate" path="skills/my-skill"
```

### list - List existing skills
```
skill_creator command="list" path="skills/"
```

## Examples

Create a new PDF editor skill:
```
skill_creator command="init" name="pdf-editor" path="skills/"
```

Package the skill:
```
skill_creator command="package" path="skills/pdf-editor"
```

Validate without packaging:
```
skill_creator command="validate" path="skills/pdf-editor"
```

## See Also

- SKILL.md in skill_creator directory for full documentation
- references/naming.md for naming guidelines
"""
        return SkillResult(success=True, output=output)
    
    elif command == "init":
        if not name:
            return SkillResult(
                success=False,
                error="name is required for init command"
            )
        
        cmd = [sys.executable, str(init_script), name, "--path", path]
        if resources:
            cmd.extend(["--resources", resources])
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).parent.parent)
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                error=result.stderr if result.returncode != 0 else None
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "package":
        if not path:
            return SkillResult(
                success=False,
                error="path is required for package command"
            )
        
        cmd = [sys.executable, str(package_script), path]
        if validate_only:
            cmd.append("--validate-only")
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).parent.parent)
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                error=result.stderr if result.returncode != 0 else None
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "validate":
        if not path:
            return SkillResult(
                success=False,
                error="path is required for validate command"
            )
        
        cmd = [sys.executable, str(package_script), path, "--validate-only"]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).parent.parent)
            )
            return SkillResult(
                success=result.returncode == 0,
                output=result.stdout + result.stderr,
                error=result.stderr if result.returncode != 0 else None
            )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    elif command == "list":
        import os
        skills_path = Path(path)
        if not skills_path.exists():
            return SkillResult(
                success=False,
                error=f"Skills directory not found: {path}"
            )
        
        skills = []
        for item in sorted(skills_path.iterdir()):
            if item.is_dir():
                skill_file = item / "SKILL.md"
                if skill_file.exists():
                    skills.append(item.name)
        
        if skills:
            output = f"# Skills in {path}\n\n" + "\n".join(f"- {s}" for s in skills)
        else:
            output = f"No skills found in {path}"
        
        return SkillResult(success=True, output=output)
    
    else:
        return SkillResult(
            success=False,
            error=f"Unknown command: {command}. Use help for available commands."
        )
