"""Skill package discovery for EFP runtime."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
from typing import Any

from ..types import SkillPackage


SKILL_FILE_NAMES = {"skill.md", "SKILL.md"}
DEFAULT_GLOBAL_SKILL_DIRECTORIES = (
    "~/.claude/skills",
    "~/.agents/skills",
    "~/.efp/skill",
    "~/.efp/skills",
)
DEFAULT_PROJECT_SKILL_DIRECTORIES = (
    ".claude/skills",
    ".agents/skills",
    ".efp/skill",
    ".efp/skills",
)
RUNTIME_SKILLS_DIR_ENV = "EFP_SKILLS_DIR"
RUNTIME_APP_SKILLS_DIR = "/app/skills"


class SkillDiscovery:
    """Discover SKILL.md/skill.md packages from configured directories."""

    def __init__(self, directories: Iterable[str | Path]):
        self.directories = [_expand_user_path(directory) for directory in directories]
        self._skills: dict[str, SkillPackage] | None = None

    def discover(self, *, refresh: bool = False) -> list[SkillPackage]:
        if self._skills is None or refresh:
            self._skills = {
                _normalize_skill_name(skill.name): skill
                for skill in discover_skills(self.directories)
            }
        return sorted(self._skills.values(), key=_skill_sort_key)

    def get(self, name: str, *, refresh: bool = False) -> SkillPackage | None:
        normalized = _normalize_skill_name(name)
        if not normalized:
            return None
        if self._skills is None or refresh:
            self.discover(refresh=refresh)
        assert self._skills is not None
        return self._skills.get(normalized)


def discover_skills(directories: Iterable[str | Path]) -> list[SkillPackage]:
    """Discover all skill packages under the configured directories."""

    packages_by_name: dict[str, SkillPackage] = {}
    seen_roots: set[Path] = set()
    for configured_dir in directories:
        directory = _expand_user_path(configured_dir)
        if not directory.exists():
            continue
        for skill_file in _iter_skill_files(directory):
            root = skill_file.parent.resolve()
            if root in seen_roots:
                continue
            skill = _load_skill_package(skill_file)
            if skill is None:
                continue
            seen_roots.add(root)
            packages_by_name[_normalize_skill_name(skill.name)] = skill
    return sorted(packages_by_name.values(), key=_skill_sort_key)


def default_skill_directories(
    workspace_root: str | Path,
    *,
    cwd: str | Path | None = None,
    include_defaults: bool = True,
) -> list[Path]:
    """Return existing default skill directories in load order."""

    if not include_defaults:
        return []
    root = _expand_user_path(workspace_root).resolve(strict=False)
    start = _ancestor_search_start(root, cwd)
    directories: list[Path] = []
    env_dir = os.getenv(RUNTIME_SKILLS_DIR_ENV)
    if env_dir and env_dir.strip():
        path = _expand_user_path(env_dir).resolve(strict=False)
        if path.is_dir():
            directories.append(path)
    app_skills = Path(RUNTIME_APP_SKILLS_DIR).resolve(strict=False)
    if app_skills.is_dir():
        directories.append(app_skills)
    for directory in DEFAULT_GLOBAL_SKILL_DIRECTORIES:
        path = _expand_user_path(directory).resolve(strict=False)
        if path.is_dir():
            directories.append(path)
    for ancestor in _project_skill_ancestors(root, start):
        for directory in DEFAULT_PROJECT_SKILL_DIRECTORIES:
            path = (ancestor / directory).resolve(strict=False)
            if path.is_dir():
                directories.append(path)
    return _dedupe_paths(directories)


def _iter_skill_files(directory: Path) -> list[Path]:
    if directory.is_file() and directory.name in SKILL_FILE_NAMES:
        return [directory]
    if not directory.is_dir():
        return []
    candidates = [
        path
        for path in directory.rglob("*")
        if (
            path.is_file()
            and path.name in SKILL_FILE_NAMES
            and not _is_hidden(path.relative_to(directory))
        )
    ]
    return sorted(
        candidates,
        key=lambda path: (str(path.parent), path.name.lower(), path.name),
    )


def _load_skill_package(skill_file: Path) -> SkillPackage | None:
    content = skill_file.read_text(encoding="utf-8")
    metadata, body = _parse_frontmatter(content)
    root = skill_file.parent
    if "name" not in metadata:
        return None
    name = str(metadata["name"]).strip()
    if not name:
        return None
    description = str(metadata["description"]) if "description" in metadata else ""
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
        if (
            path.is_file()
            and path != skill_file
            and not _is_hidden(path.relative_to(root))
        )
    ]
    return sorted(sidecars, key=lambda path: str(path.relative_to(root)))


def _is_hidden(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts)


def _ancestor_search_start(root: Path, cwd: str | Path | None) -> Path:
    if cwd is None or (isinstance(cwd, str) and not cwd.strip()):
        return root
    raw_path = _expand_user_path(cwd)
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate.resolve(strict=False)
    if resolved.is_file():
        resolved = resolved.parent
    if not _is_relative_to(resolved, root):
        return root
    return resolved


def _project_skill_ancestors(root: Path, start: Path) -> list[Path]:
    ancestors: list[Path] = []
    current = start
    while _is_relative_to(current, root):
        ancestors.append(current)
        if current == root:
            break
        current = current.parent
    return list(reversed(ancestors))


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _expand_user_path(path: str | Path) -> Path:
    text = str(path)
    if text == "~":
        return _home_path()
    if text.startswith("~/") or text.startswith("~\\"):
        return _home_path() / text[2:]
    return Path(path).expanduser()


def _home_path() -> Path:
    value = os.getenv("HOME") or os.getenv("USERPROFILE")
    return Path(value).expanduser() if value else Path.home().expanduser()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _normalize_skill_name(name: str) -> str:
    return str(name).strip().lower()


def _skill_sort_key(skill: SkillPackage) -> tuple[str, str]:
    return (_normalize_skill_name(skill.name), str(skill.skill_file))
