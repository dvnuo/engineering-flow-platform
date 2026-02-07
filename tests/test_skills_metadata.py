"""Tests for OpenClaw-style skill metadata parsing."""

import pytest
from pathlib import Path
import tempfile
import os


class TestSkillMetadataParsing:
    """Tests for skill metadata parsing from YAML frontmatter."""
    
    def test_parse_yaml_frontmatter(self):
        """Test parsing valid YAML frontmatter."""
        from skills.decorator import parse_skill_metadata, SkillMetadata
        
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("""---
name: test-skill
description: A test skill for validation
metadata:
  emoji: 🧪
  requires:
    bins: [python3, git]
    anyBins: [wget, curl]
    env: [API_KEY]
    config: [~/.config/test]
install:
  - kind: brew
    formula: test-tool
---
# Test Skill

This is a test skill.
""")
            
            metadata = parse_skill_metadata(str(skill_dir))
            
            assert metadata is not None
            assert metadata.name == "test-skill"
            assert metadata.description == "A test skill for validation"
            assert metadata.emoji == "🧪"
            assert "python3" in metadata.bins
            assert "git" in metadata.bins
            assert "wget" in metadata.any_bins
            assert "API_KEY" in metadata.env
            assert "~/.config/test" in metadata.config
            assert len(metadata.install) == 1
            assert metadata.install[0]["kind"] == "brew"
    
    def test_parse_simple_frontmatter(self):
        """Test parsing simple YAML frontmatter without metadata."""
        from skills.decorator import parse_skill_metadata
        
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "simple-skill"
            skill_dir.mkdir()
            
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("""---
name: simple
description: A simple skill
---
# Simple Skill

Description here.
""")
            
            metadata = parse_skill_metadata(str(skill_dir))
            
            assert metadata is not None
            assert metadata.name == "simple"
            assert metadata.description == "A simple skill"
    
    def test_parse_fallback_no_frontmatter(self):
        """Test fallback when no frontmatter (old format)."""
        from skills.decorator import parse_skill_metadata
        
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "old-skill"
            skill_dir.mkdir()
            
            skill_md = skill_dir / "SKILL.md"
            skill_md.write_text("""# Old Skill Format

This is an old-style skill without frontmatter.
""")
            
            # Should still parse with fallback
            metadata = parse_skill_metadata(str(skill_dir))
            
            assert metadata is not None
            assert "old" in metadata.name.lower() or metadata.markdown
    
    def test_parse_missing_skill_md(self):
        """Test that missing SKILL.md returns None."""
        from skills.decorator import parse_skill_metadata
        
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "no-skill-md"
            skill_dir.mkdir()
            
            metadata = parse_skill_metadata(str(skill_dir))
            
            assert metadata is None
    
    def test_skill_metadata_full_description(self):
        """Test full_description property with emoji."""
        from skills.decorator import SkillMetadata
        
        metadata = SkillMetadata({
            "name": "test",
            "description": "A test skill",
            "metadata": {"emoji": "🧪"}
        })
        
        assert metadata.full_description == "🧪 A test skill"
    
    def test_skill_metadata_no_emoji(self):
        """Test full_description without emoji."""
        from skills.decorator import SkillMetadata
        
        metadata = SkillMetadata({
            "name": "test",
            "description": "A test skill"
        })
        
        assert metadata.full_description == "A test skill"


class TestGetAllSkills:
    """Tests for getting all skills from directory."""
    
    def test_get_all_skills_empty(self):
        """Test with empty skills directory."""
        from skills.decorator import get_all_skills
        
        skills = get_all_skills("/nonexistent/path")
        
        assert skills == []
    
    def test_get_skill_names(self):
        """Test getting skill names."""
        from skills.decorator import get_skill_names
        
        names = get_skill_names()
        
        assert isinstance(names, list)
        # Should contain at least some known skills
        assert len(names) > 0


class TestCheckRequirements:
    """Tests for checking skill requirements."""
    
    def test_check_missing_bin(self):
        """Test checking requirement for missing binary."""
        from skills.decorator import check_skill_requirements
        
        result = check_skill_requirements("nonexistent-skill")
        
        assert result["success"] is False
        assert "not found" in result["error"].lower()
    
    def test_check_git_skill_requirements(self):
        """Test checking git skill requirements."""
        from skills.decorator import check_skill_requirements
        
        result = check_skill_requirements("git")
        
        assert result["skill"] == "git"
        assert "bins" in result
        assert "python3" in result["bins"]["available"] or "git" in result["bins"]["available"]


class TestGetSkillMetadata:
    """Tests for getting specific skill metadata."""
    
    def test_get_git_skill_metadata(self):
        """Test getting git skill metadata."""
        from skills.decorator import get_skill_metadata
        
        metadata = get_skill_metadata("git")
        
        assert metadata is not None
        assert metadata["name"] == "git"
        assert "path" in metadata
        assert "git-skill" in metadata["path"]
    
    def test_get_github_skill_metadata(self):
        """Test getting github skill metadata."""
        from skills.decorator import get_skill_metadata
        
        metadata = get_skill_metadata("github")
        
        assert metadata is not None
        assert metadata["name"] == "github"
        assert metadata["emoji"] == "🐙"
    
    def test_get_nonexistent_skill(self):
        """Test getting metadata for nonexistent skill."""
        from skills.decorator import get_skill_metadata
        
        metadata = get_skill_metadata("nonexistent-skill-xyz")
        
        assert metadata is None
