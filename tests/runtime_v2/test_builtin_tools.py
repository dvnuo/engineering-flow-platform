from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from efp_runtime.models import ToolCall
from efp_runtime.permissions import ASK, PermissionDecision, PermissionMetadata
from efp_runtime.tools.builtin import create_core_tool_registry
from efp_runtime.tools.definition import ToolContext
from efp_runtime.tools.runtime import ToolRuntime


ROOT = Path(__file__).resolve().parents[2]


class AllowEvaluator:
    async def evaluate(
        self,
        *,
        tool_id: str,
        args: dict[str, Any],
        metadata: PermissionMetadata,
        context: ToolContext | None = None,
    ) -> PermissionDecision:
        return PermissionDecision.allow()


class LocalFetchHandler(BaseHTTPRequestHandler):
    utf8_body = "hello 世界\n"
    long_body = "0123456789"

    def do_GET(self):  # noqa: N802 - http.server callback name.
        if self.path == "/utf8":
            self._send_text(self.utf8_body)
            return
        if self.path == "/long":
            self._send_text(self.long_body)
            return
        self.send_error(404)

    def log_message(self, format, *args):  # noqa: A002 - matches stdlib signature.
        return

    def _send_text(self, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def local_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalFetchHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_read_and_directory_listing_inside_workspace(tmp_path: Path):
    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "app.py").write_text("print('hello')\n", encoding="utf-8")

    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    read_result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "src/app.py"})
    )
    list_result = await runtime.execute(
        ToolCall(id="call-list", tool_id="read", args={"filePath": "src"})
    )

    assert read_result.status == "success"
    assert read_result.output["path"] == "src/app.py"
    assert read_result.output["filePath"] == "src/app.py"
    assert read_result.output["type"] == "file"
    assert read_result.output["content"] == "print('hello')\n"
    assert read_result.output["encoding"] == "utf-8"
    assert read_result.output["bytes"] == 15
    assert list_result.status == "success"
    assert list_result.output["path"] == "src"
    assert list_result.output["filePath"] == "src"
    assert list_result.output["type"] == "directory"
    assert list_result.output["entries"] == [
        {
            "name": "app.py",
            "path": "src/app.py",
            "type": "file",
            "size": 15,
        }
    ]


@pytest.mark.asyncio
async def test_path_traversal_is_rejected(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-read", tool_id="read", args={"filePath": "../outside.txt"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "Path escapes workspace root." in result.error


@pytest.mark.asyncio
async def test_write_succeeds_by_default(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))
    target = tmp_path / "created.txt"

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write",
            args={"filePath": "created.txt", "content": "blocked"},
        )
    )

    assert result.status == "success"
    assert result.success is True
    assert target.read_text(encoding="utf-8") == "blocked"


@pytest.mark.asyncio
async def test_write_explicit_ask_permission_does_not_write(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(
            tmp_path,
            write_permission=PermissionMetadata(
                action=ASK,
                category="filesystem",
                resource="workspace",
                risk="medium",
            ),
        )
    )
    target = tmp_path / "created.txt"

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write",
            args={"filePath": "created.txt", "content": "blocked"},
        )
    )

    assert result.status == "permission_requested"
    assert result.success is False
    assert result.metadata["permission_request"]["request_id"].startswith("perm_")
    assert result.metadata["permission_request"]["tool_id"] == "write"
    assert target.exists() is False


@pytest.mark.asyncio
async def test_write_succeeds_with_allow_evaluator(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-write",
            tool_id="write",
            args={
                "filePath": "notes/result.txt",
                "content": "approved\n",
            },
        )
    )

    assert result.status == "success"
    assert result.output["path"] == "notes/result.txt"
    assert result.output["bytes"] == 9
    filediff = result.output["filediff"]
    assert result.metadata["filediff"] == filediff
    assert filediff["path"] == "notes/result.txt"
    assert filediff["old_path"] == "notes/result.txt"
    assert filediff["additions"] == 1
    assert filediff["deletions"] == 0
    assert "+approved" in filediff["patch"]
    assert (tmp_path / "notes/result.txt").read_text(encoding="utf-8") == "approved\n"


@pytest.mark.asyncio
async def test_grep_finds_matches(tmp_path: Path):
    (tmp_path / "a.txt").write_text("alpha\nneedle here\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("no match\n", encoding="utf-8")
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-grep", tool_id="grep", args={"pattern": "needle", "path": "."})
    )

    assert result.status == "success"
    assert result.output["pattern"] == "needle"
    assert result.output["path"] == "."
    assert result.output["matches"] == [
        {
            "path": "a.txt",
            "line_number": 2,
            "column": 1,
            "line": "needle here",
        }
    ]
    assert result.output["files_searched"] == 2
    assert result.output["truncated"] is False
    assert result.output["include"] is None
    assert result.output["total_matches"] == 1
    assert result.output["returned_matches"] == 1
    assert result.content == "Found 1 matches\na.txt:\n  Line 2: needle here"


@pytest.mark.asyncio
async def test_shell_succeeds_by_default(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-shell", tool_id="bash", args={"command": "printf ok", "description": "Print ok"})
    )

    assert result.status == "success"
    assert result.success is True
    assert result.output["stdout"] == "ok"


@pytest.mark.asyncio
async def test_shell_explicit_ask_permission_requests_approval(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(
            tmp_path,
            shell_permission=PermissionMetadata(
                action=ASK,
                category="shell",
                resource="workspace",
                risk="high",
            ),
        )
    )

    result = await runtime.execute(
        ToolCall(id="call-shell", tool_id="bash", args={"command": "printf ok", "description": "Print ok"})
    )

    assert result.status == "permission_requested"
    assert result.success is False
    assert result.metadata["permission_request"]["request_id"].startswith("perm_")
    assert result.metadata["permission_request"]["tool_id"] == "bash"


@pytest.mark.asyncio
async def test_shell_succeeds_with_allow_evaluator(tmp_path: Path):
    runtime = ToolRuntime(
        create_core_tool_registry(tmp_path),
        permission_evaluator=AllowEvaluator(),
    )

    result = await runtime.execute(
        ToolCall(
            id="call-shell",
            tool_id="bash",
            args={"command": "printf 'ok\\n'", "description": "Print ok", "timeout": 5000},
        )
    )

    assert result.status == "success"
    assert result.output["stdout"] == "ok\n"
    assert result.output["stderr"] == ""
    assert result.output["exit_code"] == 0
    assert result.output["timed_out"] is False
    assert result.output["cwd"] == "."
    assert isinstance(result.output["duration_ms"], int)
    assert result.metadata["cwd"] == "."
    assert result.metadata["timed_out"] is False
    assert result.metadata["truncated"] is False
    assert result.content == "<stdout>\nok\n</stdout>"


@pytest.mark.asyncio
async def test_invalid_tool_returns_model_visible_argument_error(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-invalid",
            tool_id="invalid",
            args={"tool": "read", "error": "filePath must be a string"},
        )
    )

    expected = (
        "The arguments provided to the read tool are invalid: "
        "filePath must be a string"
    )
    assert result.status == "success"
    assert result.success is True
    assert result.content == expected
    assert result.output == {
        "tool": "read",
        "error": "filePath must be a string",
        "message": expected,
    }


@pytest.mark.asyncio
async def test_webfetch_reads_utf8_text_from_local_http_server(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))
    url = f"{local_http_server}/utf8"

    result = await runtime.execute(
        ToolCall(id="call-webfetch", tool_id="webfetch", args={"url": url})
    )

    body = LocalFetchHandler.utf8_body
    assert result.status == "success"
    assert result.content == body
    assert result.output == {
        "url": url,
        "status_code": 200,
        "content_type": "text/plain; charset=utf-8",
        "content": body,
        "bytes": len(body.encode("utf-8")),
        "truncated": False,
        "original_chars": len(body),
    }
    assert result.metadata["original_chars"] == len(body)


@pytest.mark.asyncio
async def test_webfetch_rejects_non_http_urls(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(id="call-webfetch", tool_id="webfetch", args={"url": "file:///etc/passwd"})
    )

    assert result.status == "error"
    assert result.success is False
    assert "http:// or https://" in result.error


@pytest.mark.asyncio
async def test_webfetch_rejects_model_visible_headers(tmp_path: Path):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))

    result = await runtime.execute(
        ToolCall(
            id="call-fetch",
            tool_id="webfetch",
            args={"url": "http://example.test", "headers": {"X-Test": "1"}},
        )
    )

    assert result.status == "validation_error"
    assert "Unexpected argument(s): headers" in result.error


@pytest.mark.asyncio
async def test_webfetch_rejects_model_visible_max_chars(
    tmp_path: Path,
    local_http_server: str,
):
    runtime = ToolRuntime(create_core_tool_registry(tmp_path))
    url = f"{local_http_server}/long"

    result = await runtime.execute(
        ToolCall(
            id="call-fetch",
            tool_id="webfetch",
            args={"url": url, "max_chars": 4},
        )
    )

    assert result.status == "validation_error"
    assert "Unexpected argument(s): max_chars" in result.error


def test_builtin_tools_import_standalone_without_legacy_modules():
    code = """
import json
import sys
from pathlib import Path

from efp_runtime.tools.builtin import create_core_tool_registry

registry = create_core_tool_registry(Path(".").resolve())
legacy_modules = [
    "src.agents.core",
    "src.bash_tools",
    "src.github",
    "src.jira",
    "src.confluence",
    "src.git",
    "src.context_tools",
]
print(json.dumps({
    "ids": registry.ids(),
    "legacy_loaded": [name for name in legacy_modules if name in sys.modules],
}))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = "src"

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "ids": [
            "apply_patch",
            "bash",
            "edit",
            "glob",
            "grep",
            "invalid",
            "read",
            "skill",
            "task",
            "todowrite",
            "webfetch",
            "write",
        ],
        "legacy_loaded": [],
    }


def test_builtin_tool_source_stays_inside_runtime_v2_boundary():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/efp_runtime/tools/builtin").rglob("*.py"))
    )
    forbidden_tokens = [
        "from src.efp_runtime",
        "import src.efp_runtime",
        "src.agents.core",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
        "src.agents.tool_result_policy",
        "src.bash_tools",
        "src.github",
        "src.jira",
        "src.confluence",
        "src.git",
        "src.context_tools",
    ]

    for token in forbidden_tokens:
        assert token not in combined
