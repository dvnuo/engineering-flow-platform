"""Skill package discovery for EFP Runtime v2."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..types import SkillPackage


SKILL_FILE_NAMES = {"skill.md", "SKILL.md"}
DEFAULT_PROJECT_SKILL_DIRECTORIES = (
    ".opencode/skills",
    ".claude/skills",
    ".agents/skills",
)


class SkillDiscovery:
    """Discover SKILL.md/skill.md packages from configured directories."""

    def __init__(self, directories: Iterable[str | Path]):
        self.directories = [Path(directory).expanduser() for directory in directories]
        self._skills: dict[str, SkillPackage] | None = None

    def discover(self, *, refresh: bool = False) -> list[SkillPackage]:
        if self._skills is None or refresh:
            self._skills = {skill.name: skill for skill in discover_skills(self.directories)}
        return [self._skills[name] for name in sorted(self._skills)]

    def get(self, name: str, *, refresh: bool = False) -> SkillPackage | None:
        normalized = name.strip()
        if not normalized:
            return None
        if self._skills is None or refresh:
            self.discover(refresh=refresh)
        assert self._skills is not None
        if normalized in self._skills:
            return self._skills[normalized]
        lower_map = {key.lower(): key for key in self._skills}
        matched = lower_map.get(normalized.lower())
        if matched is None:
            return None
        return self._skills[matched]


def discover_skills(directories: Iterable[str | Path]) -> list[SkillPackage]:
    """Discover all skill packages under the configured directories."""

    packages: list[SkillPackage] = []
    seen_roots: set[Path] = set()
    for configured_dir in directories:
        directory = Path(configured_dir).expanduser()
        if not directory.exists():
            continue
        for skill_file in _iter_skill_files(directory):
            root = skill_file.parent.resolve()
            if root in seen_roots:
                continue
            seen_roots.add(root)
            packages.append(_load_skill_package(skill_file))
    packages.sort(key=lambda skill: (skill.name.lower(), str(skill.skill_file)))
    return packages


def default_skill_directories(
    workspace_root: str | Path,
    *,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing project-local default skill directories in load order."""

    if not include_defaults:
        return []
    root = Path(workspace_root).expanduser().resolve(strict=False)
    directories: list[Path] = []
    for directory in DEFAULT_PROJECT_SKILL_DIRECTORIES:
        path = (root / directory).resolve(strict=False)
        if path.is_dir():
            directories.append(path)
    return directories


def _iter_skill_files(directory: Path) -> list[Path]:
    if directory.is_file() and directory.name in SKILL_FILE_NAMES:
        return [directory]
    if not directory.is_dir():
        return []
    candidates = [
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name in SKILL_FILE_NAMES and not _is_hidden(path)
    ]
    return sorted(candidates, key=lambda path: (str(path.parent), path.name.lower()))


def _load_skill_package(skill_file: Path) -> SkillPackage:
    content = skill_file.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(content)
    root = skill_file.parent
    name = str(metadata.get("name") or root.name).strip()
    description = str(metadata.get("description") or "").strip()
    sidecars = _collect_sidecars(root, skill_file)
    return SkillPackage(
        name=name,
        description=description,
        root=root,
        skill_file=skill_file,
        content=body.strip("\n"),
        sidecar_files=sidecars,
        metadata=metadata,
    )


def _parse_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    lines = content.splitlines()
    if not lines:
        return {}, ""

    if lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                metadata = _parse_simple_yaml_lines(lines[1:index])
                body = "\n".join(lines[index + 1 :])
                return metadata, body
        return {}, content

    metadata_lines: list[str] = []
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            break
        if ":" not in stripped:
            body_start = 0
            metadata_lines = []
            break
        key = stripped.split(":", 1)[0].strip()
        if not _is_simple_metadata_key(key):
            body_start = 0
            metadata_lines = []
            break
        metadata_lines.append(line)
    if metadata_lines and any(
        line.strip().split(":", 1)[0].strip() in {"name", "description"}
        for line in metadata_lines
    ):
        return _parse_simple_yaml_lines(metadata_lines), "\n".join(lines[body_start:])
    return {}, content


def _parse_simple_yaml_lines(lines: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for line in lines:
        if line[:1].isspace():
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not _is_simple_metadata_key(key):
            continue
        if not value and key not in {"name", "description"}:
            continue
        if not _is_simple_scalar_value(value):
            continue
        metadata[key] = _strip_yaml_string(value)
    return metadata


def _is_simple_metadata_key(key: str) -> bool:
    return bool(key) and all(
        char.isalnum() or char in {"_", "-"} for char in key
    )


def _is_simple_scalar_value(value: str) -> bool:
    if not value:
        return True
    if value in {"|", "|-", "|+", ">", ">-", ">+"}:
        return False
    return value[0] not in {"[", "{"}


def _strip_yaml_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _collect_sidecars(root: Path, skill_file: Path) -> list[Path]:
    sidecars = [
        path
        for path in root.rglob("*")
        if path.is_file() and path != skill_file and not _is_hidden(path)
    ]
    return sorted(sidecars, key=lambda path: str(path.relative_to(root)))


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)
