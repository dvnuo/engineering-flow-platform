"""Workspace-contained LSP navigation tool for EFP runtime."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ...lsp import (
    LSPClient,
    LSPPosition,
    LSPRequest,
    LSP_OPERATIONS,
    is_lsp_client_available,
)
from ...permissions import ALLOW, PermissionMetadata
from ..definition import ToolContext, ToolDef
from .filesystem import (
    normalize_workspace_root,
    resolve_workspace_path,
    workspace_relative_path,
)


NO_LSP_CLIENT_MESSAGE = "No LSP client available for this file type."
_POSITION_OPERATIONS = frozenset(
    operation
    for operation in LSP_OPERATIONS
    if operation not in {"documentSymbol", "workspaceSymbol"}
)


def create_lsp_tool(
    workspace_root: str | Path,
    *,
    client: LSPClient | None = None,
    permission: PermissionMetadata | None = None,
    tool_id: str = "lsp",
) -> ToolDef:
    """Create an injectable LSP navigation tool without starting a server."""

    root = normalize_workspace_root(workspace_root)

    async def execute(args: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        operation = args["operation"]
        file_path_arg = args.get("filePath")
        query = args.get("query")
        resolved_path: Path | None = None
        relative_path: str | None = None

        if operation != "workspaceSymbol" and not file_path_arg:
            raise ValueError(f"filePath is required for {operation}.")
        if file_path_arg:
            resolved_path = _resolve_lsp_file(root, file_path_arg)
            relative_path = workspace_relative_path(root, resolved_path)

        position = None
        if operation in _POSITION_OPERATIONS:
            if resolved_path is None:
                raise ValueError(f"filePath is required for {operation}.")
            line = _required_one_based_integer(args, "line", operation)
            character = _required_one_based_integer(args, "character", operation)
            position = LSPPosition(
                file_path=str(resolved_path),
                line=line,
                character=character,
            )

        client_file_path = str(resolved_path) if resolved_path is not None else None
        if not await is_lsp_client_available(client, client_file_path):
            raise RuntimeError(NO_LSP_CLIENT_MESSAGE)

        request = LSPRequest(
            operation=operation,
            file_path=client_file_path,
            position=position,
            query=query,
            metadata=_request_metadata(
                context,
                root=root,
                relative_path=relative_path,
                position=position,
            ),
        )
        result = client.execute(request)  # type: ignore[union-attr]
        if inspect.isawaitable(result):
            result = await result

        result_count = _result_count(result)
        output: dict[str, Any] = {
            "operation": operation,
            "file_path": relative_path,
            "query": query,
            "result": result,
            "result_count": result_count,
        }
        if position is not None:
            output["line"] = position.line
            output["character"] = position.character
        if result_count == 0:
            output["message"] = f"No results found for {operation}"
        return output

    return ToolDef(
        id=tool_id,
        description="Run code navigation queries through an injected LSP client.",
        input_schema={
            "type": "object",
            "required": ["operation"],
            "properties": {
                "operation": {"type": "string", "enum": list(LSP_OPERATIONS)},
                "filePath": {"type": "string"},
                "line": {"type": "integer"},
                "character": {"type": "integer"},
                "query": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        permission=permission
        or PermissionMetadata(
            action=ALLOW,
            category="lsp",
            resource="workspace",
            risk="low",
        ),
    )


def _resolve_lsp_file(workspace_root: Path, file_path: str) -> Path:
    path = resolve_workspace_path(workspace_root, file_path)
    if not path.exists():
        raise FileNotFoundError(
            f"File does not exist: {workspace_relative_path(workspace_root, path)}"
        )
    if not path.is_file():
        raise IsADirectoryError(
            f"Path is not a file: {workspace_relative_path(workspace_root, path)}"
        )
    return path


def _required_one_based_integer(
    args: dict[str, Any],
    name: str,
    operation: str,
) -> int:
    value = args.get(name)
    if value is None:
        raise ValueError(f"{name} is required for {operation}.")
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer for {operation}.")
    if value < 1:
        raise ValueError(f"{name} must be greater than or equal to 1 for {operation}.")
    return value


def _request_metadata(
    context: ToolContext,
    *,
    root: Path,
    relative_path: str | None,
    position: LSPPosition | None,
) -> dict[str, Any]:
    metadata = context.to_metadata()
    metadata["workspace_root"] = str(root)
    metadata["workspace_relative_path"] = relative_path
    if position is not None:
        metadata["zero_based_line"] = position.line - 1
        metadata["zero_based_character"] = position.character - 1
        metadata["zero_based_position"] = {
            "line": position.line - 1,
            "character": position.character - 1,
        }
    return metadata


def _result_count(result: Any) -> int:
    if result is None:
        return 0
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        return 1 if result else 0
    if isinstance(result, str):
        return 1 if result else 0
    return 1


__all__ = ["NO_LSP_CLIENT_MESSAGE", "create_lsp_tool"]
