"""Tests for Skill Creator Skill."""

import pytest
import tempfile
import os
from pathlib import Path


class TestSkillCreatorInit:
    """Tests for init_skill.py script."""
    
    def test_normalize_skill_name(self):
        """Test skill name normalization."""
        from skills.skill_creator.scripts.init_skill import normalize_skill_name
        
        assert normalize_skill_name("My Skill") == "my-skill"
        assert normalize_skill_name("PDF Editor") == "pdf-editor"
        assert normalize_skill_name("git-branch-manager") == "git-branch-manager"
        assert normalize_skill_name("My--Skill") == "my-skill"
    
    def test_create_skill_template(self):
        """Test skill template creation."""
        from skills.skill_creator.scripts.init_skill import create_skill_template
        
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir)
            skill_dir = create_skill_template(
                name="test-skill",
                output_path=output_path,
                resources=["scripts", "references"]
            )
            
            assert skill_dir.name == "test-skill"
            assert (skill_dir / "SKILL.md").exists()
            assert (skill_dir / "scripts").exists()
            assert (skill_dir / "references").exists()
            assert not (skill_dir / "assets").exists()  # Not requested


class TestSkillCreatorPackage:
    """Tests for package_skill.py script."""
    
    def test_validate_skill_name(self):
        """Test skill name validation."""
        from skills.skill_creator.scripts.package_skill import validate_skill_name
        
        assert validate_skill_name("my-skill") is True
        assert validate_skill_name("pdf-editor-v2") is True
        assert validate_skill_name("MySkill") is False  # Uppercase
        assert validate_skill_name("my_skill") is False  # Underscore
        assert validate_skill_name("my--skill") is False  # Double hyphen
        assert validate_skill_name("my-skill-") is False  # Trailing hyphen
        assert validate_skill_name("a" * 64) is True
        assert validate_skill_name("a" * 65) is False  # Too long
    
    def test_parse_frontmatter(self):
        """Test YAML frontmatter parsing."""
        from skills.skill_creator.scripts.package_skill import parse_frontmatter
        
        yaml_str = """
name: test-skill
description: A test skill
metadata:
  emoji: 🧪
"""
        result = parse_frontmatter(yaml_str)
        
        assert result["name"] == "test-skill"
        assert result["description"] == "A test skill"
        assert result["metadata"]["emoji"] == "🧪"


class TestSkillCreatorSkill:
    """Tests for skill_creator function."""
    
    def test_help_command(self):
        """Test help command."""
        from skills.skill_creator.skill import skill_creator
        from skills.decorator import SkillResult
        
        result = skill_creator(command="help")
        
        assert isinstance(result, SkillResult)
        assert result.success is True
        assert "Skill Creator Commands" in result.output
    
    def test_list_command(self):
        """Test list command."""
        from skills.skill_creator.skill import skill_creator
        from skills.decorator import SkillResult
        
        result = skill_creator(command="list", path="/nonexistent")
        
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "not found" in result.error.lower()
    
    def test_unknown_command(self):
        """Test unknown command."""
        from skills.skill_creator.skill import skill_creator
        from skills.decorator import SkillResult
        
        result = skill_creator(command="unknown")
        
        assert isinstance(result, SkillResult)
        assert result.success is False
        assert "Unknown command" in result.error


class TestSkillNaming:
    """Tests for skill naming conventions."""
    
    def test_good_names(self):
        """Test good skill names."""
        from skills.skill_creator.scripts.package_skill import validate_skill_name
        
        good_names = [
            "git-branch-manager",
            "pdf-editor",
            "docker-deploy-v2",
            "api文档生成器",  # Unicode allowed
        ]
        
        for name in good_names:
            assert validate_skill_name(name) is True, f"Expected {name} to be valid"
    
    def test_bad_names(self):
        """Test bad skill names."""
        from skills.skill_creator.scripts.package_skill import validate_skill_name
        
        bad_names = [
            "GitBranch",  # Uppercase
            "git_branch",  # Underscore
            "my--skill",  # Double hyphen
            "my-skill-",  # Trailing hyphen
        ]
        
        for name in bad_names:
            assert validate_skill_name(name) is False, f"Expected {name} to be invalid"
