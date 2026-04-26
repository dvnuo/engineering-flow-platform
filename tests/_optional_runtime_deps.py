from __future__ import annotations

import pytest


def has_ruamel_yaml() -> bool:
    try:
        import ruamel.yaml  # noqa: F401

        return True
    except Exception:
        return False


def skip_if_missing_ruamel_yaml(
    message: str = "full runtime dependencies unavailable (missing ruamel.yaml)",
) -> None:
    if not has_ruamel_yaml():
        pytest.skip(message, allow_module_level=True)
