"""External skills keep their own layout; the runtime reads them wherever they live.

A skill mounted outside the workspace (Portal puts them under /app/skills) may
carry dozens of images next to a handful of documents. The model must still be
able to open the documents with the ordinary file tools and see them first in
the skill listing, without the skill author arranging files to suit the runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from efp_runtime.skills.discovery import SkillDiscovery
from efp_runtime.skills.tool import build_skill_tool
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.registry import ToolRegistry
from efp_runtime.tools.runtime import ToolRuntime
from efp_runtime.types import ToolCall


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256))


def _brand_like_skill(skills_root: Path, *, icons: int = 45) -> Path:
    skill = skills_root / "pptx-brand"
    (skill / "assets" / "icons").mkdir(parents=True)
    (skill / "references").mkdir()
    (skill / "scripts").mkdir()
    (skill / "skill.md").write_text(
        "---\nname: pptx-brand\ndescription: Brand decks\n---\n# Brand\nRead references/style-guide.md first.\n",
        encoding="utf-8",
    )
    for index in range(icons):
        (skill / "assets" / "icons" / f"icon-{index:02}.png").write_bytes(PNG_BYTES)
    (skill / "assets" / "cover.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
    (skill / "references" / "style-guide.md").write_text("# Style guide\nNavy and orange.\n", encoding="utf-8")
    (skill / "references" / "styles.json").write_text('{"styles": {"formal": {}}}\n', encoding="utf-8")
    (skill / "scripts" / "build.py").write_text("print('hi')\n", encoding="utf-8")
    return skill


@pytest.fixture
def layout(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skills_root = tmp_path / "app-skills"
    skills_root.mkdir()
    skill = _brand_like_skill(skills_root)
    (tmp_path / "secret.txt").write_text("not for the model\n", encoding="utf-8")
    return workspace, skills_root, skill


async def _call(registry, tool_id: str, **args):
    return await ToolRuntime(registry).execute(ToolCall(id=f"call-{tool_id}", tool_id=tool_id, args=args))


def _posix(path: Path) -> str:
    return path.resolve().as_posix()


@pytest.mark.asyncio
async def test_read_opens_files_and_directories_inside_a_skill(layout):
    workspace, skills_root, skill = layout
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])

    result = await _call(registry, "read", filePath=str(skill / "references" / "style-guide.md"))

    assert result.status == "success", result.error
    assert "Navy and orange." in result.content
    # Paths outside the workspace are reported absolute, so the model can reuse them.
    assert result.output["path"] == _posix(skill / "references" / "style-guide.md")

    listing = await _call(registry, "read", filePath=str(skill))

    assert listing.status == "success", listing.error
    names = {entry["name"] for entry in listing.output["entries"]}
    assert {"assets", "references", "scripts", "skill.md"} <= names
    assert all(entry["path"].startswith(_posix(skill)) for entry in listing.output["entries"])


@pytest.mark.asyncio
async def test_read_tool_advertises_the_read_only_roots(layout):
    workspace, skills_root, _skill = layout
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])

    assert str(skills_root.resolve()) in registry.require("read").description
    assert str(skills_root.resolve()) in registry.require("glob").description
    assert str(skills_root.resolve()) in registry.require("grep").description

    # A skill directory inside the workspace is reachable already and is not advertised twice.
    inside = workspace / ".efp" / "skills"
    inside.mkdir(parents=True)
    plain = create_core_tool_registry(workspace, skill_directories=[inside])
    assert "read-only" not in plain.require("read").description


@pytest.mark.asyncio
async def test_everything_else_outside_the_workspace_stays_rejected(layout, tmp_path):
    workspace, skills_root, _skill = layout
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])

    absolute = await _call(registry, "read", filePath=str(tmp_path / "secret.txt"))
    relative = await _call(registry, "read", filePath="../secret.txt")

    for result in (absolute, relative):
        assert result.status == "error"
        assert "Path escapes workspace root." in result.error
        assert str(skills_root.resolve()) in result.error


@pytest.mark.asyncio
async def test_write_never_reaches_a_skill_directory(layout):
    workspace, skills_root, skill = layout
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])
    target = skill / "references" / "style-guide.md"

    result = await _call(registry, "write", filePath=str(target), content="overwritten")

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error
    assert target.read_text(encoding="utf-8") == "# Style guide\nNavy and orange.\n"


@pytest.mark.asyncio
async def test_glob_and_grep_search_a_skill_directory(layout, tmp_path):
    workspace, skills_root, skill = layout
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])

    globbed = await _call(registry, "glob", pattern="**/*.md", path=str(skill))

    assert globbed.status == "success", globbed.error
    assert _posix(skill / "references" / "style-guide.md") in globbed.output["matches"]
    assert _posix(skill / "skill.md") in globbed.output["matches"]

    grepped = await _call(registry, "grep", pattern="Navy", path=str(skill))

    assert grepped.status == "success", grepped.error
    assert grepped.output["matches"] == [
        {
            "path": _posix(skill / "references" / "style-guide.md"),
            "line_number": 2,
            "column": 1,
            "line": "Navy and orange.",
        }
    ]

    outside = await _call(registry, "grep", pattern="model", path=str(tmp_path))
    assert outside.status == "error"
    assert "Path escapes workspace root." in outside.error


@pytest.mark.asyncio
async def test_a_symlink_out_of_a_skill_directory_is_still_rejected(layout, tmp_path):
    workspace, skills_root, skill = layout
    link = skill / "references" / "leak.md"
    try:
        os.symlink(tmp_path / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    registry = create_core_tool_registry(workspace, skill_directories=[skills_root])

    result = await _call(registry, "read", filePath=str(link))

    assert result.status == "error"
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_skill_tool_lists_documentation_first_and_summarizes_the_rest(layout):
    _workspace, skills_root, skill = layout
    runtime = ToolRuntime(ToolRegistry([build_skill_tool(SkillDiscovery([skills_root]))]))

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="skill", args={"name": "pptx-brand", "include_sidecar_content": True})
    )

    assert result.status == "success", result.error
    file_lines = [line for line in result.content.splitlines() if line.startswith("<file>")]
    assert file_lines[:3] == [
        f"<file>{skill / 'references' / 'style-guide.md'}</file>",
        f"<file>{skill / 'references' / 'styles.json'}</file>",
        f"<file>{skill / 'scripts' / 'build.py'}</file>",
    ]
    assert len(file_lines) == 40
    assert "Navy and orange." in result.content
    # 49 sidecars in total: 40 listed, the 9 remaining icons summarized by directory.
    assert '<omitted_files count="9">' in result.content
    assert '<directory path="assets/icons" files="9" types=".png"/>' in result.content
    assert "The base directory is read-only" in result.content


@pytest.mark.asyncio
async def test_skill_tool_returns_one_file_by_relative_path(layout):
    _workspace, skills_root, skill = layout
    runtime = ToolRuntime(ToolRegistry([build_skill_tool(SkillDiscovery([skills_root]))]))

    result = await runtime.execute(
        ToolCall(id="call-1", tool_id="skill", args={"name": "pptx-brand", "file": "references/style-guide.md"})
    )

    assert result.status == "success", result.error
    assert result.content.startswith('<skill_file name="pptx-brand"')
    assert "Navy and orange." in result.content
    assert result.output["relative_path"] == "references/style-guide.md"
    assert result.output["path"] == str((skill / "references" / "style-guide.md").resolve())
    assert result.metadata["file"] == "references/style-guide.md"

    truncated = await runtime.execute(
        ToolCall(
            id="call-2",
            tool_id="skill",
            args={"name": "pptx-brand", "file": "references/style-guide.md", "max_file_chars": 7},
        )
    )
    assert truncated.output["content"] == "# Style"
    assert truncated.output["truncated"] is True

    escape = await runtime.execute(
        ToolCall(id="call-3", tool_id="skill", args={"name": "pptx-brand", "file": "../secret.txt"})
    )
    assert escape.status == "error"
    assert "outside the skill directory" in escape.error

    binary = await runtime.execute(
        ToolCall(id="call-4", tool_id="skill", args={"name": "pptx-brand", "file": "assets/cover.jpg"})
    )
    assert binary.status == "error"
    assert "binary" in binary.error

    missing = await runtime.execute(
        ToolCall(id="call-5", tool_id="skill", args={"name": "pptx-brand", "file": "references/nope.md"})
    )
    assert missing.status == "error"
    assert "Skill file not found: references/nope.md" in missing.error
