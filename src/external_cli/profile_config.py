"""Project runtime-profile data into external CLI configuration via real CLIs."""

from __future__ import annotations

import hashlib
import configparser
import json
import os
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from ruamel.yaml import YAML

from src.external_cli.github import github_hostname_from_base_url
from src.utils.proxy import no_proxy_value, proxy_url_with_credentials


_MANAGED_BY = "efp_runtime_profile"
_METADATA_VERSION = 2
_GIT_INCLUDE_BEGIN = "# BEGIN EFP_RUNTIME_PROFILE_GIT_INCLUDE"
_GIT_INCLUDE_END = "# END EFP_RUNTIME_PROFILE_GIT_INCLUDE"
_REDACTED_SECRET = "[REDACTED_SECRET]"
_PROXY_URL_ENV_KEYS = ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY")
_PROFILE_SECRET_KEY_NAMES = frozenset({"api_key", "password", "token", "api_token", "access_token", "secret"})
_AWS_AUTH_DEFAULT_COMMAND = "aws-auth"
_RUNTIME_VENV_BIN_DIRS = ("/app/venv/bin", "/opt/venv/bin")
_yaml = YAML()
_yaml.default_flow_style = False


def _home_path() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


@dataclass(frozen=True)
class _CliEnvironment:
    env: dict[str, str]
    secrets: tuple[str, ...] = ()


def apply_runtime_profile_external_config(
    profile_config: dict[str, Any],
    *,
    config_path: Path | str | None = None,
) -> None:
    """Apply external CLI config for the sanitized Portal profile using real CLIs."""

    cli_environment = _build_cli_environment(profile_config, config_path=config_path)
    previous = _load_metadata()
    if previous:
        clear_runtime_profile_external_config(metadata=previous, cli_environment=cli_environment)

    metadata: dict[str, Any] = {"version": _METADATA_VERSION, "managed_by": _MANAGED_BY}

    # NOTE: jira/confluence are intentionally NOT projected through CLI writes
    # anymore; those CLIs read the EFP_CONFIG_JSON env blob exported at boot.
    try:
        _apply_github(profile_config, metadata=metadata, cli_environment=cli_environment)
        _apply_aws(profile_config, metadata=metadata, cli_environment=cli_environment)
        _apply_git_user(profile_config, metadata=metadata, cli_environment=cli_environment)
    except Exception:
        if _metadata_has_managed_entries(metadata):
            _write_private_text(_metadata_path(), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        raise

    if _metadata_has_managed_entries(metadata):
        _write_private_text(_metadata_path(), json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    else:
        _remove_file_if_exists(_metadata_path())


def clear_runtime_profile_external_config(
    *,
    metadata: dict[str, Any] | None = None,
    cli_environment: _CliEnvironment | None = None,
    config_path: Path | str | None = None,
) -> None:
    """Remove external CLI config previously managed by runtime profile apply."""

    meta = metadata if isinstance(metadata, dict) else _load_metadata()
    if not meta or meta.get("managed_by") != _MANAGED_BY:
        return

    cli_environment = cli_environment or _build_cli_environment(None, config_path=config_path)
    _clear_new_metadata(meta, cli_environment=cli_environment)
    _clear_legacy_metadata(meta)
    _remove_file_if_exists(_metadata_path())


def redact_runtime_profile_external_config_error(
    error: Any,
    profile_config: dict[str, Any] | None = None,
) -> str:
    """Return a status-safe external CLI error message."""

    return _truncate(_redact_text(str(error), _profile_config_redaction_secrets(profile_config)))


def build_tools_config_json(effective_config: dict[str, Any]) -> dict[str, Any]:
    """Build the EFP_CONFIG_JSON payload for the Go CLI tools.

    The shape matches ``RootConfig`` in engineering-flow-platform-tools
    (internal/config/config.go): top-level keys version/jira/confluence/
    jenkins/aws/visual/mobile-auto. Jira/Confluence sections are transformed
    from the profile shape into the tools instances shape; the other sections
    are taken from the effective config verbatim. Empty sections are omitted.
    """
    root: dict[str, Any] = {}
    if not isinstance(effective_config, dict):
        return root

    version = effective_config.get("version")
    if isinstance(version, int) and not isinstance(version, bool):
        root["version"] = version

    for product in ("jira", "confluence"):
        section = effective_config.get(product)
        instances = _build_product_instances(section, product=product)
        if not instances:
            continue
        root[product] = {
            "default_instance": _default_instance_name(section, instances),
            "instances": [_tools_instance_config(instance, product=product) for instance in instances],
        }

    for section_name in ("jenkins", "aws", "visual", "mobile-auto"):
        section = effective_config.get(section_name)
        if isinstance(section, dict) and section:
            root[section_name] = json.loads(json.dumps(section))

    return root


def _tools_instance_config(instance: dict[str, Any], *, product: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": instance["name"],
        "base_url": instance["base_url"],
        "rest_path": instance["rest_path"],
    }
    if product == "jira":
        out["api_version"] = instance["api_version"]

    auth = instance.get("auth") if isinstance(instance.get("auth"), dict) else {}
    auth_type = str(auth.get("type") or "")
    if auth_type:
        auth_out: dict[str, Any] = {"type": auth_type}
        username = str(auth.get("username") or "")
        if username:
            auth_out["username"] = username
        secret = str(auth.get("secret") or "")
        if secret:
            secret_field = {
                "basic_password": "password",
                "basic_api_key": "api_key",
                "bearer_token": "token",
            }.get(auth_type)
            if secret_field:
                auth_out[secret_field] = secret
        out["auth"] = auth_out
    return out


def _remove_atlassian_instance(product: str, name: str, *, cli_environment: _CliEnvironment) -> None:
    _run_cli(
        [product, "--json", "instance", "remove", name, "--yes"],
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )


def _remove_atlassian_instance_if_exists(product: str, name: str, *, cli_environment: _CliEnvironment) -> None:
    try:
        _run_cli(
            [product, "--json", "instance", "remove", name, "--yes"],
            allowed_returncodes=tuple(range(256)),
            env=cli_environment.env,
            env_secrets=cli_environment.secrets,
        )
    except RuntimeError:
        return


def _apply_github(
    profile_config: dict[str, Any],
    *,
    metadata: dict[str, Any],
    cli_environment: _CliEnvironment,
) -> None:
    login = _build_gh_login(profile_config)
    if login is None:
        return
    host, token = login
    _run_cli(
        ["gh", "auth", "login", "--hostname", host, "--with-token", "--git-protocol", "https"],
        input_text=token,
        secrets=(token,),
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )
    metadata["gh"] = {"hosts": [host]}
    _run_cli(
        ["gh", "auth", "setup-git", "--hostname", host],
        secrets=(token,),
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )


def _logout_gh_host(host: str, *, cli_environment: _CliEnvironment) -> None:
    _run_cli(
        ["gh", "auth", "logout", "--hostname", host],
        input_text="y\n",
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )


def _apply_git_user(
    profile_config: dict[str, Any],
    *,
    metadata: dict[str, Any],
    cli_environment: _CliEnvironment,
) -> None:
    git_user = _extract_git_user(profile_config)
    if git_user is None:
        return
    name, email = git_user
    previous = {
        "user.name": _git_config_get("user.name", cli_environment=cli_environment),
        "user.email": _git_config_get("user.email", cli_environment=cli_environment),
    }
    metadata["git"] = {
        "managed": {
            "user.name": name,
            "user.email": email,
        },
        "previous": previous,
    }
    _git_config_set("user.name", name, cli_environment=cli_environment)
    _git_config_set("user.email", email, cli_environment=cli_environment)


def _restore_git_user(git_meta: dict[str, Any], *, cli_environment: _CliEnvironment) -> None:
    managed = git_meta.get("managed") if isinstance(git_meta.get("managed"), dict) else {}
    previous = git_meta.get("previous") if isinstance(git_meta.get("previous"), dict) else {}
    for key in ("user.name", "user.email"):
        managed_value = _string_or_empty(managed.get(key))
        current_value = _git_config_get(key, cli_environment=cli_environment)
        if managed_value and current_value not in (None, managed_value):
            continue
        previous_value = previous.get(key)
        if isinstance(previous_value, str) and previous_value:
            _git_config_set(key, previous_value, cli_environment=cli_environment)
        else:
            _git_config_unset(key, cli_environment=cli_environment)


def _git_config_get(key: str, *, cli_environment: _CliEnvironment) -> str | None:
    result = _run_cli_result(
        ["git", "config", "--global", "--get", key],
        allowed_returncodes=(0, 1),
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    return value or None


def _git_config_set(key: str, value: str, *, cli_environment: _CliEnvironment) -> None:
    _run_cli(
        ["git", "config", "--global", key, value],
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )


def _git_config_unset(key: str, *, cli_environment: _CliEnvironment) -> None:
    _run_cli(
        ["git", "config", "--global", "--unset", key],
        allowed_returncodes=(0, 5),
        env=cli_environment.env,
        env_secrets=cli_environment.secrets,
    )


def _clear_new_metadata(meta: dict[str, Any], *, cli_environment: _CliEnvironment) -> None:
    for product in ("jira", "confluence"):
        product_meta = meta.get(product) if isinstance(meta.get(product), dict) else {}
        for name in _metadata_instance_names(product_meta):
            _remove_atlassian_instance(product, name, cli_environment=cli_environment)

    gh = meta.get("gh") if isinstance(meta.get("gh"), dict) else {}
    if "path" not in gh:
        for host in _metadata_gh_hosts(gh):
            _logout_gh_host(host, cli_environment=cli_environment)

    aws = meta.get("aws") if isinstance(meta.get("aws"), dict) else {}
    if aws:
        _restore_aws_profile(aws)

    git = meta.get("git") if isinstance(meta.get("git"), dict) else {}
    if "managed" in git:
        _restore_git_user(git, cli_environment=cli_environment)


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
    return any(key in metadata for key in ("jira", "confluence", "gh", "aws", "jenkins", "git"))


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


def _build_cli_environment(
    profile_config: dict[str, Any] | None,
    *,
    config_path: Path | str | None = None,
) -> _CliEnvironment:
    env = os.environ.copy()
    if config_path is not None:
        env["EFP_CONFIG"] = str(config_path)
    proxy_config = profile_config.get("proxy") if isinstance(profile_config, dict) else None
    if not isinstance(proxy_config, dict):
        return _CliEnvironment(env=env)

    proxy_url = _string_or_empty(proxy_config.get("url"))
    if not proxy_config.get("enabled") or not proxy_url:
        return _CliEnvironment(env=env)

    final_proxy_url = proxy_url_with_credentials(
        proxy_url,
        proxy_config.get("username"),
        proxy_config.get("password"),
    )
    for key in _PROXY_URL_ENV_KEYS:
        env[key] = final_proxy_url
    no_proxy = no_proxy_value(proxy_config)
    env["no_proxy"] = no_proxy
    env["NO_PROXY"] = no_proxy

    return _CliEnvironment(
        env=env,
        secrets=_combine_secrets(_proxy_redaction_secrets(proxy_config, final_proxy_url)),
    )


def _proxy_redaction_secrets(proxy_config: dict[str, Any], final_proxy_url: str) -> tuple[str, ...]:
    secrets: list[str] = []
    raw_url = _string_or_empty(proxy_config.get("url"))
    if final_proxy_url:
        secrets.append(final_proxy_url)
    if raw_url:
        secrets.append(raw_url)
    password = proxy_config.get("password")
    if password:
        password_text = str(password)
        secrets.append(password_text)
        secrets.append(quote(password_text, safe=""))
    return tuple(secrets)


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
        base_url = _profile_instance_base_url(raw)
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


def _profile_instance_base_url(raw: dict[str, Any]) -> str:
    return _normalize_base_url(raw.get("base_url") or raw.get("baseUrl") or raw.get("url") or raw.get("uri"))


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


def _apply_aws(
    profile_config: dict[str, Any],
    *,
    metadata: dict[str, Any],
    cli_environment: _CliEnvironment,
) -> None:
    aws_config = _build_aws_config(profile_config)
    if aws_config is None:
        return

    credentials_path = _aws_credentials_path()
    credentials_section = "default"

    previous_credentials = _read_ini_section(credentials_path, credentials_section)

    metadata["aws"] = {
        "auth_type": "aws_auth_cli",
        "command": _format_command(aws_config["command"], (aws_config["password"],)),
        "previous": {},
    }
    if previous_credentials is not None:
        metadata["aws"]["credentials_path"] = str(credentials_path)
        metadata["aws"]["credentials_section"] = credentials_section
        metadata["aws"]["previous"]["credentials"] = previous_credentials

    try:
        _run_cli(
            aws_config["command"],
            input_text=aws_config["password"],
            env=_aws_env(cli_environment),
            secrets=(aws_config["password"],),
            env_secrets=cli_environment.secrets,
        )
    except Exception:
        _restore_aws_profile(metadata["aws"])
        raise


def _build_aws_config(profile_config: dict[str, Any]) -> dict[str, Any] | None:
    aws = profile_config.get("aws") if isinstance(profile_config, dict) else None
    if not isinstance(aws, dict) or aws.get("enabled") is False:
        return None
    domain = _single_line(aws.get("domain"))
    username = _single_line(aws.get("username"))
    password = _string_or_empty(aws.get("password"))
    if not domain or not username or not password:
        return None
    return {
        "domain": domain,
        "username": username,
        "password": password,
        "command": _aws_configure_command(domain=domain, username=username),
    }


def _aws_configure_command(*, domain: str, username: str) -> list[str]:
    return [
        _AWS_AUTH_DEFAULT_COMMAND,
        "auth",
        "login",
        "--domain",
        domain,
        "--username",
        username,
        "--password-stdin",
        "--json",
    ]


def _aws_env(cli_environment: _CliEnvironment) -> dict[str, str]:
    env = dict(cli_environment.env)
    for key in ("AD_PASS", "password"):
        env.pop(key, None)
    env["PATH"] = _path_with_runtime_venv_bins(env.get("PATH", ""))
    return env


def _path_with_runtime_venv_bins(path_value: str) -> str:
    parts = [part for part in str(path_value or "").split(os.pathsep) if part]
    prefix = [path for path in _RUNTIME_VENV_BIN_DIRS if path not in parts]
    return os.pathsep.join(prefix + parts)


def _restore_aws_profile(aws_meta: dict[str, Any]) -> None:
    profile_name = _string_or_empty(aws_meta.get("profile")) or "default"
    config_section = _string_or_empty(aws_meta.get("config_section")) or _aws_config_section_name(profile_name)
    credentials_section = _string_or_empty(aws_meta.get("credentials_section")) or profile_name
    previous = aws_meta.get("previous") if isinstance(aws_meta.get("previous"), dict) else {}

    previous_config = previous.get("config") if isinstance(previous.get("config"), dict) else None
    previous_credentials = previous.get("credentials") if isinstance(previous.get("credentials"), dict) else None

    config_path_value = aws_meta.get("config_path")
    if config_path_value or previous_config is not None:
        config_path = Path(str(config_path_value or _aws_config_path()))
        if previous_config is None:
            _remove_ini_section(config_path, config_section)
        else:
            _set_ini_section_exact(config_path, config_section, previous_config)

    credentials_path_value = aws_meta.get("credentials_path")
    if credentials_path_value or previous_credentials is not None:
        credentials_path = Path(str(credentials_path_value or _aws_credentials_path()))
        if previous_credentials is None:
            _remove_ini_section(credentials_path, credentials_section)
        else:
            _set_ini_section_exact(credentials_path, credentials_section, previous_credentials)

    for key in ("auth_path", "helper_path"):
        raw_path = aws_meta.get(key)
        if raw_path:
            _remove_file_if_exists(Path(str(raw_path)))


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
    env: dict[str, str] | None = None,
    env_secrets: tuple[str, ...] = (),
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    return _run_cli_result(
        args,
        input_text=input_text,
        secrets=secrets,
        env=env,
        env_secrets=env_secrets,
        allowed_returncodes=allowed_returncodes,
    )


def _run_cli_result(
    args: list[str],
    *,
    input_text: str | None = None,
    secrets: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    env_secrets: tuple[str, ...] = (),
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    redaction_secrets = _combine_secrets(secrets, env_secrets)
    try:
        result = subprocess.run(
            args,
            input=input_text,
            text=True,
            capture_output=True,
            check=False,
            env=env,
        )
    except OSError as exc:
        raise RuntimeError(
            "Failed to run external CLI command: "
            f"{_format_command(args, redaction_secrets)}: {_redact_text(str(exc), redaction_secrets)}"
        ) from exc

    if result.returncode not in allowed_returncodes:
        stdout = _redact_text((result.stdout or "").strip(), redaction_secrets)
        stderr = _redact_text((result.stderr or "").strip(), redaction_secrets)
        details = []
        if stdout:
            details.append(f"stdout: {_truncate(stdout)}")
        if stderr:
            details.append(f"stderr: {_truncate(stderr)}")
        detail_text = "; ".join(details)
        suffix = f". {detail_text}" if detail_text else ""
        raise RuntimeError(
            "External CLI command failed: "
            f"{_format_command(args, redaction_secrets)} exited with {result.returncode}{suffix}"
        )
    return result


def _format_command(args: list[str], secrets: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(_redact_text(str(arg), secrets)) for arg in args)


def _redact_text(value: str, secrets: tuple[str, ...]) -> str:
    text = str(value or "")
    for secret in _combine_secrets(secrets):
        text = text.replace(secret, _REDACTED_SECRET)
    return text


def _profile_config_redaction_secrets(profile_config: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(profile_config, dict):
        return ()
    secrets: list[str] = []
    _collect_profile_config_redaction_secrets(profile_config, secrets)
    secrets.extend(_profile_proxy_redaction_secrets(profile_config))
    return _combine_secrets(tuple(secrets))


def _collect_profile_config_redaction_secrets(value: Any, secrets: list[str], key: str = "") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            child_key = str(raw_key)
            if _is_profile_secret_key(child_key) and not isinstance(child, (dict, list, tuple)):
                secret = "" if child is None else str(child)
                if secret:
                    secrets.append(secret)
            _collect_profile_config_redaction_secrets(child, secrets, child_key)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_profile_config_redaction_secrets(item, secrets, key)


def _is_profile_secret_key(key: str) -> bool:
    normalized = "".join(ch for ch in str(key or "").lower() if ch.isalnum())
    secret_markers = ("apikey", "password", "token", "apitoken", "accesstoken", "secret", "access")
    return any(marker in normalized for marker in secret_markers) or str(key or "").lower() in _PROFILE_SECRET_KEY_NAMES


def _profile_proxy_redaction_secrets(profile_config: dict[str, Any]) -> tuple[str, ...]:
    proxy_config = profile_config.get("proxy") if isinstance(profile_config, dict) else None
    if not isinstance(proxy_config, dict):
        return ()

    secrets: list[str] = []
    raw_url = _string_or_empty(proxy_config.get("url"))
    if raw_url:
        secrets.append(raw_url)
        parsed = urlparse(raw_url)
        if parsed.password:
            secrets.append(parsed.password)
            secrets.append(quote(parsed.password, safe=""))
        try:
            final_proxy_url = proxy_url_with_credentials(
                raw_url,
                proxy_config.get("username"),
                proxy_config.get("password"),
            )
        except Exception:
            final_proxy_url = ""
        if final_proxy_url:
            secrets.append(final_proxy_url)

    password = proxy_config.get("password")
    if password:
        password_text = str(password)
        secrets.append(password_text)
        secrets.append(quote(password_text, safe=""))
    return tuple(secrets)


def _combine_secrets(*secret_groups: tuple[str, ...]) -> tuple[str, ...]:
    secrets: set[str] = set()
    for group in secret_groups:
        for secret in group:
            if secret:
                secrets.add(str(secret))
    return tuple(sorted(secrets, key=len, reverse=True))


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


def _aws_config_path() -> Path:
    return _home_path() / ".aws" / "config"


def _aws_credentials_path() -> Path:
    return _home_path() / ".aws" / "credentials"


def _aws_config_section_name(profile_name: str) -> str:
    profile = _string_or_empty(profile_name) or "default"
    return "default" if profile == "default" else f"profile {profile}"


def _new_ini_parser() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.optionxform = str
    return parser


def _read_ini(path: Path) -> configparser.RawConfigParser:
    parser = _new_ini_parser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    return parser


def _read_ini_section(path: Path, section: str) -> dict[str, str] | None:
    parser = _read_ini(path)
    if not parser.has_section(section):
        return None
    return {key: value for key, value in parser.items(section)}


def _write_ini(path: Path, parser: configparser.RawConfigParser) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        parser.write(handle)
    _chmod_private(path)


def _set_ini_section_exact(path: Path, section: str, values: dict[str, Any]) -> None:
    parser = _read_ini(path)
    if parser.has_section(section):
        parser.remove_section(section)
    parser.add_section(section)
    for key, value in values.items():
        text = _string_or_empty(value)
        if text:
            parser.set(section, key, text)
    _write_ini(path, parser)


def _remove_ini_section(path: Path, section: str) -> None:
    if not path.exists():
        return
    parser = _read_ini(path)
    if not parser.has_section(section):
        return
    parser.remove_section(section)
    if parser.sections():
        _write_ini(path, parser)
    else:
        _remove_file_if_exists(path)


def _metadata_path() -> Path:
    return _home_path() / ".config" / "efp" / "runtime-profile-external-config.json"


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
