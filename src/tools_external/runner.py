from __future__ import annotations

import importlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from .contracts import ExternalToolExecutionResult, ToolDescriptor

_LAST_TOOLS_DIR: Optional[str] = None
_RESERVED_ARGS = {
    "_session_id",
    "_message_id",
    "_task_id",
    "_runtime_type",
    "_workspace_dir",
    "_portal_metadata",
    "_opencode_context",
}


def _ensure_python_path(tools_dir: Optional[Path]) -> None:
    global _LAST_TOOLS_DIR
    if tools_dir is None:
        return
    py_dir = tools_dir / "python"
    if not py_dir.exists() or not py_dir.is_dir():
        return
    if _LAST_TOOLS_DIR and _LAST_TOOLS_DIR != str(tools_dir):
        for key in list(sys.modules.keys()):
            if key == "efp_tools" or key.startswith("efp_tools."):
                sys.modules.pop(key, None)
    _LAST_TOOLS_DIR = str(tools_dir)
    py_dir_str = str(py_dir)
    if py_dir_str in sys.path:
        sys.path.remove(py_dir_str)
    sys.path.insert(0, py_dir_str)


def _build_execution_context(kwargs: dict[str, Any], tools_dir: Optional[Path]) -> dict[str, Any]:
    workspace_dir = kwargs.get("_workspace_dir") or os.getenv("EFP_WORKSPACE_DIR") or str(Path.home() / ".efp" / "workspace")
    portal_metadata = kwargs.get("_portal_metadata") if isinstance(kwargs.get("_portal_metadata"), dict) else {}
    repo_root = Path(__file__).resolve().parents[2]
    context_blob_dir = os.getenv("EFP_CONTEXT_BLOB_DIR") or str(Path(workspace_dir) / "context_blobs")
    return {
        "runtime_type": kwargs.get("_runtime_type") or "native",
        "session_id": kwargs.get("_session_id"),
        "message_id": kwargs.get("_message_id"),
        "task_id": kwargs.get("_task_id"),
        "workspace_dir": str(workspace_dir),
        "portal_metadata": {
            **portal_metadata,
            "legacy_runtime_src_dir": str(repo_root),
            "context_blob_dir": context_blob_dir,
        },
        "opencode_context": kwargs.get("_opencode_context") if isinstance(kwargs.get("_opencode_context"), dict) else {},
    }


def _strip_framework_args(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if k not in _RESERVED_ARGS}


def _normalize_result(value: Any, tool_name: str) -> ExternalToolExecutionResult:
    if isinstance(value, ExternalToolExecutionResult):
        return value
    if value is None:
        return ExternalToolExecutionResult(success=False, error=f"External tool '{tool_name}' returned no result")
    if hasattr(value, "to_dict") and callable(getattr(value, "to_dict")):
        value = value.to_dict()
    if hasattr(value, "success") and (hasattr(value, "content") or hasattr(value, "data") or hasattr(value, "error")):
        data = {"success": getattr(value, "success", True), "content": getattr(value, "content", ""), "data": getattr(value, "data", None), "error": getattr(value, "error", None), "artifacts": getattr(value, "artifacts", None)}
        value = data
    if isinstance(value, dict):
        if any(k in value for k in ("success", "content", "data", "error", "artifacts")):
            success = bool(value.get("success", True))
            content = value.get("content")
            if (content is None or content == "") and value.get("data") is not None:
                content = json.dumps(value.get("data"), ensure_ascii=False, default=str)
            if content is None:
                content = ""
            return ExternalToolExecutionResult(success=success, content=str(content), error=value.get("error"))
        return ExternalToolExecutionResult(success=True, content=json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, str):
        return ExternalToolExecutionResult(success=True, content=value)
    if isinstance(value, (list, int, float, bool)):
        return ExternalToolExecutionResult(success=True, content=json.dumps(value, ensure_ascii=False, default=str))
    return ExternalToolExecutionResult(success=True, content=str(value))


async def execute_python_entrypoint(descriptor: ToolDescriptor, tools_dir: Optional[Path], **kwargs: Any) -> ExternalToolExecutionResult:
    try:
        _ensure_python_path(tools_dir)
        context = _build_execution_context(kwargs, tools_dir)
        filtered_args = _strip_framework_args(kwargs)

        efp_runner = (tools_dir / "python" / "efp_tools" / "runner.py") if tools_dir else None
        if efp_runner and efp_runner.exists():
            runner = importlib.import_module("efp_tools.runner")
            fn = getattr(runner, "execute_tool_async", None) or getattr(runner, "execute_tool", None)
            if fn:
                result = fn(tools_dir=str(tools_dir), tool=descriptor.name, args=filtered_args, context=context)
                if inspect.isawaitable(result):
                    result = await result
                return _normalize_result(result, descriptor.name)

        if not descriptor.python_entrypoint:
            return ExternalToolExecutionResult(success=False, error=f"External tool '{descriptor.name}' has no python_entrypoint")

        module_name, func_name = descriptor.python_entrypoint.split(":", 1)
        root_package = module_name.split(".", 1)[0]
        for loaded in list(sys.modules.keys()):
            if loaded == module_name or loaded.startswith(f"{module_name}.") or loaded == root_package or loaded.startswith(f"{root_package}."):
                sys.modules.pop(loaded, None)
        module = importlib.import_module(module_name)
        func = getattr(module, func_name)
        sig = inspect.signature(func)
        call_kwargs: dict[str, Any] = {}
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if accepts_kwargs:
            call_kwargs.update(filtered_args)
        else:
            for p in sig.parameters:
                if p in filtered_args:
                    call_kwargs[p] = filtered_args[p]
        if "context" in sig.parameters:
            call_kwargs["context"] = context
        if "_session_id" in sig.parameters and context.get("session_id") is not None:
            call_kwargs["_session_id"] = context.get("session_id")
        result = func(**call_kwargs)
        if inspect.isawaitable(result):
            result = await result
        return _normalize_result(result, descriptor.name)
    except (KeyError, PermissionError, Exception) as exc:
        return ExternalToolExecutionResult(success=False, content="", error=str(exc))
