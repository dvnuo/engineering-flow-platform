from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Optional

from .contracts import ExternalToolExecutionResult, ToolDescriptor


def _ensure_python_path(tools_dir: Optional[Path]) -> None:
    if tools_dir is None:
        return
    py_dir = tools_dir / "python"
    if not py_dir.exists() or not py_dir.is_dir():
        return
    py_dir_str = str(py_dir)
    if py_dir_str not in sys.path:
        sys.path.insert(0, py_dir_str)


def _normalize_result(value: Any) -> ExternalToolExecutionResult:
    if isinstance(value, ExternalToolExecutionResult):
        return value
    if isinstance(value, str):
        return ExternalToolExecutionResult(success=True, content=value)
    if isinstance(value, (dict, list, int, float, bool)):
        return ExternalToolExecutionResult(success=True, content=json.dumps(value, ensure_ascii=False, default=str))
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        data = value.to_dict()
        if not isinstance(data, dict):
            return ExternalToolExecutionResult(success=True, content=str(data))
        content = data.get("content") or data.get("output") or json.dumps(data, ensure_ascii=False, default=str)
        return ExternalToolExecutionResult(success=bool(data.get("success", True)), content=str(content), error=data.get("error"))
    if all(hasattr(value, attr) for attr in ("success", "content", "error")):
        content = "" if getattr(value, "content") is None else str(getattr(value, "content"))
        return ExternalToolExecutionResult(
            success=bool(getattr(value, "success")),
            content=content,
            error=getattr(value, "error"),
        )
    return ExternalToolExecutionResult(success=True, content=str(value))


async def execute_python_entrypoint(
    descriptor: ToolDescriptor,
    tools_dir: Optional[Path],
    **kwargs: Any,
) -> ExternalToolExecutionResult:
    if not descriptor.python_entrypoint:
        return ExternalToolExecutionResult(
            success=False,
            error=f"External tool '{descriptor.name}' has no python_entrypoint",
        )

    try:
        _ensure_python_path(tools_dir)
        module_name, func_name = descriptor.python_entrypoint.split(":", 1)
        root_package = module_name.split(".", 1)[0]
        for loaded in list(sys.modules.keys()):
            if loaded == module_name or loaded.startswith(f"{module_name}.") or loaded == root_package or loaded.startswith(f"{root_package}."):
                sys.modules.pop(loaded, None)
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        result = func(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return _normalize_result(result)
    except Exception as exc:
        return ExternalToolExecutionResult(success=False, content="", error=str(exc))
