"""Project runtime-profile data into external CLI configuration via real CLIs."""

from __future__ import annotations

import hashlib
import json
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from src.external_cli.github import github_hostname_from_base_url


_MANAGED_BY = "efp_runtime_profile"
_METADATA_VERSION = 2
_GIT_INCLUDE_BEGIN = "# BEGIN EFP_RUNTIME_PROFILE_GIT_INCLUDE"
_GIT_INCLUDE_END = "# END EFP_RUNTIME_PROFILE_GIT_INCLUDE"
_REDACTED_SECRET = "[REDACTED_SECRET]"
_yaml = YAML()
_yaml.default_flow_style = False


def apply_runtime_profile_external_config(profile_config: dict[str, Any]) -> None:
    """Apply external CLI config for the sanitized Portal profile using real CLIs."""

    previous = _load_metadata()
    if previous:
        clear_runtime_profile_external_config(metadata=previous)

    metadata: dict[str, Any] = {"version": _METADATA_VERSION, "managed_by": _MANAGED_BY}

    try:
        _apply_atlassian_product(profile_config, product="jira", metadata=metadata)
        _apply_atlassian_product(profile_config, product="confluence", metadata=metadata)
        _apply_github(profile_config, metadata=metadata)
        _apply_git_user(profile_config, metadata=metadata)
    except Exception:
        if _metadata_has_managed_entries(metadata):
            _write_private_text(_metadata_path(), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        raise

    if _metadata_has_managed_entries(metadata):
        _write_private_text(_metadata_path(), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    else:
        _remove_file_if_exists(_metadata_path())


def clear_runtime_profile_external_config(*, metadata: dict[str, Any] | None = None) -> None:
    """Remove external CLI config previously managed by runtime profile apply."""

    meta = metadata if isinstance(metadata, dict) else _load_metadata()
    if not meta or meta.get("managed_by") != _MANAGED_BY:
        return

    _clear_new_metadata(meta)
    _clear_legacy_metadata(meta)
    _remove_file_if_exists(_metadata_path())


def _apply_atlassian_product(profile_config: dict[str, Any], *, product: str, metadata: dict[str, Any]) -> None:
    instances = _build_product_instances(profile_config.get(product), product=product)
    if not instances:
        return

    default_name = _default_instance_name(profile_config.get(product), instances)
    product_meta: dict[str, Any] = {"instances": []}
    metadata[product] = product_meta
    for instance in instances:
        _add_atlassian_instance(product, instance, default=instance["name"] == default_name)
        product_meta["instances"].append({"name": instance["name"]})


def _add_atlassian_instance(product: str, instance: dict[str, Any], *, default: bool) -> None:
    args = [
        product,
        "--json",
        "instance",
        "add",
        instance["name"],
        "--base-url",
        instance["base_url"],
        "--rest-path",
        instance["rest_path"],
    ]
    if product == "jira":
        args.extend(["--api-version", instance["api_version"]])
    if default:
        args.append("--default")

    auth = instance.get("auth") if isinstance(instance.get("auth"), dict) else {}
    secret = str(auth.get("secret") or "")
    if auth.get("type"):
        args.extend(["--auth-type", str(auth["type"])])
    username = str(auth.get("username") or "")
    if username:
        args.extend(["--username", username])
    stdin_text = None
    if secret:
        args.append(str(auth["stdin_flag"]))
        stdin_text = secret

    _run_cli(args, input_text=stdin_text, secrets=(secret,))


def _remove_atlassian_instance(product: str, name: str) -> None:
    _run_cli([product, "--json", "instance", "remove", name, "--yes"])


def _apply_github(profile_config: dict[str, Any], *, metadata: dict[str, Any]) -> None:
    login = _build_gh_login(profile_config)
    if login is None:
        return
    host, token = login
    _run_cli(
        ["gh", "auth", "login", "--hostname", host, "--with-token", "--git-protocol", "https"],
        input_text=token,
        secrets=(token,),
    )
    metadata["gh"] = {"hosts": [host]}
    _run_cli(["gh", "auth", "setup-git", "--hostname", host], secrets=(token,))


def _logout_gh_host(host: str) -> None:
    _run_cli(["gh", "auth", "logout", "--hostname", host], input_text="y\n")


def _apply_git_user(profile_config: dict[str, Any], *, metadata: dict[str, Any]) -> None:
    git_user = _extract_git_user(profile_config)
    if git_user is None:
        return
    name, email = git_user
    previous = {
        "user.name": _git_config_get("user.name"),
        "user.email": _git_config_get("user.email"),
    }
    metadata["git"] = {
        "managed": {
            "user.name": name,
            "user.email": email,
        },
        "previous": previous,
    }
    _git_config_set("user.name", name)
    _git_config_set("user.email", email)


def _restore_git_user(git_meta: dict[str, Any]) -> None:
    managed = git_meta.get("managed") if isinstance(git_meta.get("managed"), dict) else {}
    previous = git_meta.get("previous") if isinstance(git_meta.get("previous"), dict) else {}
    for key in ("user.name", "user.email"):
        managed_value = _string_or_empty(managed.get(key))
        current_value = _git_config_get(key)
        if managed_value and current_value not in (None, managed_value):
            continue
        previous_value = previous.get(key)
        if isinstance(previous_value, str) and previous_value:
            _git_config_set(key, previous_value)
        else:
            _git_config_unset(key)


def _git_config_get(key: str) -> str | None:
    result = _run_cli_result(["git", "config", "--global", "--get", key], allowed_returncodes=(0, 1))
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def _git_config_set(key: str, value: str) -> None:
    _run_cli(["git", "config", "--global", key, value])


def _git_config_unset(key: str) -> None:
    _run_cli(["git", "config", "--global", "--unset", key], allowed_returncodes=(0, 5))


def _clear_new_metadata(meta: dict[str, Any]) -> None:
    for product in ("jira", "confluence"):
        product_meta = meta.get(product) if isinstance(meta.get(product), dict) else {}
        for name in _metadata_instance_names(product_meta):
            _remove_atlassian_instance(product, name)

    gh = meta.get("gh") if isinstance(meta.get("gh"), dict) else {}
    if "path" not in gh:
        for host in _metadata_gh_hosts(gh):
            _logout_gh_host(host)

    git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
    if "managed" in git:
        _restore_git_user(git)


def _clear_legacy_metadata(meta: dict[str, Any]) -> None:
    atlassian = meta.get("atlassian") if isinstance(meta.get("atlassian"), dict) else {}
    atlassian_path = Path(str(atlassian.get("path") or "")) if atlassian.get("path") else None
    atlassian_sha = str(atlassian.get("sha256") or "")
    if atlassian_path and atlassian_path.exists() and _sha256_path(atlassian_path) == atlassian_sha:
        _remove_file_if_exists(atlassian_path)

    gh = meta.get("gh") if isinstance(meta.get("gh"), dict) else {}
    gh_path = Path(str(gh.get("path") or "")) if gh.get("path") else None
    gh_hosts = gh.get("hosts") if isinstance(gh.get("hosts"), dict) else {}
    if gh_path and gh_path.exists() and gh_hosts:
        hosts = _read_yaml_mapping(gh_path)
        changed = False
        for host, host_meta in gh_hosts.items():
            expected_sha = str((host_meta or {}).get("token_sha256") or "") if isinstance(host_meta, dict) else ""
            current = hosts.get(host)
            if isinstance(current, dict) and _sha256_text(str(current.get("oauth_token") or "")) == expected_sha:
                hosts.pop(host, None)
                changed = True
        if changed:
            if hosts:
                _write_private_yaml(gh_path, hosts)
            else:
                _remove_file_if_exists(gh_path)

    git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
    generated_git = Path(str(git.get("generated_path") or "")) if git.get("generated_path") else None
    gitconfig_path = Path(str(git.get("gitconfig_path") or "")) if git.get("gitconfig_path") else None
    if gitconfig_path:
        _remove_git_include(gitconfig_path)
    if generated_git:
        _remove_file_if_exists(generated_git)


def _metadata_has_managed_entries(metadata: dict[str, Any]) -> bool:
    return any(key in metadata for key in ("jira", "confluence", "gh", "git"))


def _metadata_instance_names(product_meta: dict[str, Any]) -> list[str]:
    raw = product_meta.get("instances")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        name = ""
        if isinstance(item, dict):
            name = _string_or_empty(item.get("name"))
        else:
            name = _string_or_empty(item)
        if name:
            names.append(name)
    return names


def _metadata_gh_hosts(gh_meta: dict[str, Any]) -> list[str]:
    raw = gh_meta.get("hosts")
    if isinstance(raw, list):
        return [host for host in (_string_or_empty(item) for item in raw) if host]
    if isinstance(raw, dict):
        return [host for host in (_string_or_empty(item) for item in raw.keys()) if host]
    host = _string_or_empty(gh_meta.get("host"))
    return [host] if host else []


def _build_product_instances(product_config: Any, *, product: str) -> list[dict[str, Any]]:
    if not isinstance(product_config, dict):
        return []
    if product_config.get("enabled") is False:
        return []
    raw_instances = product_config.get("instances")
    if not isinstance(raw_instances, list):
        return []
    instances: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for index, raw in enumerate(raw_instances, 1):
        if not isinstance(raw, dict) or raw.get("enabled") is False:
            continue
        base_url = _normalize_base_url(raw.get("base_url") or raw.get("url"))
        if not base_url:
            continue
        name = _unique_instance_name(str(raw.get("name") or f"{product}-{index}").strip(), used_names, product, index)
        auth = _build_auth(raw)
        if product == "jira":
            api_version = "3" if str(raw.get("api_version") or "").strip() == "3" else "2"
            instance = {
                "name": name,
                "base_url": base_url,
                "api_version": api_version,
                "rest_path": str(raw.get("rest_path") or f"/rest/api/{api_version}"),
                "auth": auth,
            }
        else:
            instance = {
                "name": name,
                "base_url": base_url,
                "rest_path": str(raw.get("rest_path") or "/rest/api"),
                "auth": auth,
            }
        instances.append(instance)
    return instances


def _unique_instance_name(raw_name: str, used_names: set[str], product: str, index: int) -> str:
    candidate = raw_name or f"{product}-{index}"
    base = candidate
    suffix = 2
    while candidate in used_names:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _default_instance_name(product_config: Any, instances: list[dict[str, Any]]) -> str:
    if not instances:
        return ""
    preferred = ""
    if isinstance(product_config, dict):
        preferred = str(product_config.get("default_instance") or "").strip()
    names = {str(item.get("name") or "") for item in instances}
    return preferred if preferred in names else str(instances[0].get("name") or "")


def _build_auth(raw: dict[str, Any]) -> dict[str, str]:
    username = _string_or_empty(raw.get("username"))
    password = _string_or_empty(raw.get("password"))
    api_key = _string_or_empty(raw.get("api_key") or raw.get("api_token"))
    token = _string_or_empty(raw.get("token") or raw.get("access_token"))
    if username and password:
        return {
            "type": "basic_password",
            "username": username,
            "secret": password,
            "stdin_flag": "--password-stdin",
        }
    if username and (api_key or token):
        return {
            "type": "basic_api_key",
            "username": username,
            "secret": api_key or token,
            "stdin_flag": "--api-key-stdin",
        }
    if token or api_key:
        return {
            "type": "bearer_token",
            "secret": token or api_key,
            "stdin_flag": "--token-stdin",
        }
    return {}


def _build_gh_login(profile_config: dict[str, Any]) -> tuple[str, str] | None:
    github = profile_config.get("github") if isinstance(profile_config, dict) else None
    if not isinstance(github, dict) or github.get("enabled") is False:
        return None
    token = _string_or_empty(github.get("api_token") or github.get("token") or github.get("access_token"))
    if not token:
        return None
    host = github_hostname_from_base_url(_string_or_empty(github.get("api_base_url") or github.get("base_url")))
    return host, token


def _extract_git_user(profile_config: dict[str, Any]) -> tuple[str, str] | None:
    git = profile_config.get("git") if isinstance(profile_config, dict) else None
    user = git.get("user") if isinstance(git, dict) and isinstance(git.get("user"), dict) else None
    if not isinstance(user, dict):
        return None
    name = _single_line(user.get("name"))
    email = _single_line(user.get("email"))
    if not name or not email:
        return None
    return name, email


def _run_cli(
    args: list[str],
    *,
    input_text: str | None = None,
    secrets: tuple[str, ...] = (),
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    return _run_cli_result(
        args,
        input_text=input_text,
        secrets=secrets,
        allowed_returncodes=allowed_returncodes,
    )


def _run_cli_result(
    args: list[str],
    *,
    input_text: str | None = None,
    secrets: tuple[str, ...] = (),
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Failed to run external CLI command: {_format_command(args, secrets)}: {_redact_text(str(exc), secrets)}"
        ) from exc

    if result.returncode not in allowed_returncodes:
        stdout = _redact_text((result.stdout or "").strip(), secrets)
        stderr = _redact_text((result.stderr or "").strip(), secrets)
        details = []
        if stdout:
            details.append(f"stdout: {_truncate(stdout)}")
        if stderr:
            details.append(f"stderr: {_truncate(stderr)}")
        detail_text = "; ".join(details)
        suffix = f". {detail_text}" if detail_text else ""
        raise RuntimeError(
            f"External CLI command failed: {_format_command(args, secrets)} exited with {result.returncode}{suffix}"
        )
    return result


def _format_command(args: list[str], secrets: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(_redact_text(str(arg), secrets)) for arg in args)


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, _REDACTED_SECRET)
    return text


def _truncate(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def _normalize_base_url(value: Any) -> str:
    text = _string_or_empty(value)
    return text.rstrip("/")


def _string_or_empty(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _single_line(value: Any) -> str:
    return _string_or_empty(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")


def _metadata_path() -> Path:
    return Path.home() / ".config" / "efp" / "runtime-profile-external-config.json"


def _write_private_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)
    _chmod_private(path)


def _write_private_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml.dump(payload, handle)
    _chmod_private(path)


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        loaded = _yaml.load(handle) or {}
    return loaded if isinstance(loaded, dict) else {}


def _chmod_private(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def _load_metadata() -> dict[str, Any]:
    path = _metadata_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_path(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _remove_file_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _remove_git_include(gitconfig_path: Path) -> None:
    if not gitconfig_path.exists():
        return
    original = gitconfig_path.read_text(encoding="utf-8")
    cleaned = _strip_git_include_block(original).strip()
    gitconfig_path.write_text((cleaned + "\n") if cleaned else "", encoding="utf-8")
    _chmod_private(gitconfig_path)


def _strip_git_include_block(text: str) -> str:
    lines = str(text or "").splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == _GIT_INCLUDE_BEGIN:
            skipping = True
            continue
        if skipping and line.strip() == _GIT_INCLUDE_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)
