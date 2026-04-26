from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _module(name: str, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def load_webchat_lightweight():
    src_pkg = _module("src")
    src_pkg.__path__ = []
    gateway_pkg = _module("src.gateway")
    gateway_pkg.__path__ = []

    class _Cfg:
        def __init__(self):
            self._config = {}

        def get(self, key, default=None):
            cur = self._config
            for part in str(key).split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return default
                cur = cur[part]
            return cur

        def get_effective_config(self):
            return dict(self._config)

        def get_managed_overlay_meta(self):
            return {}

        def set_managed_overlay(self, *_args, **_kwargs):
            return []

        def clear_managed_overlay(self):
            return None

    cfg = _Cfg()

    async def _noop_async(*_args, **_kwargs):
        return None

    modules = {
        "src": src_pkg,
        "src.gateway": gateway_pkg,
        "src.utils.file_parser.storage": _module(
            "src.utils.file_parser.storage",
            init_storage=lambda: None,
            _file_metadata={},
            StoredFileNotFoundError=RuntimeError,
            get_metadata=lambda *_a, **_k: None,
        ),
        "src.utils.file_parser": _module("src.utils.file_parser", parse_file=_noop_async),
        "src.file_artifacts.service": _module(
            "src.file_artifacts.service",
            bind_artifact_to_session=lambda *_a, **_k: None,
            register_existing_file_as_artifact=lambda *_a, **_k: None,
            update_projection_from_parse_result=lambda *_a, **_k: None,
        ),
        "src.utils.truncate": _module("src.utils.truncate", truncate=lambda x, *_a, **_k: x),
        "src.utils.redaction": _module(
            "src.utils.redaction",
            safe_preview=lambda x, *_a, **_k: x,
            safe_log_field=lambda x, *_a, **_k: x,
            sanitize_exception_message=lambda x, *_a, **_k: str(x),
        ),
        "src.utils.logger": _module("src.utils.logger", clear_log_context=lambda: None, set_log_context=lambda **_k: None),
        "src.agents.core": _module("src.agents.core", Agent=object, run_chat_execution=_noop_async),
        "src.hooks.session_memory": _module("src.hooks.session_memory", save_session_summary=_noop_async),
        "src.agents.errors": _module("src.agents.errors", extract_error_details=lambda *_a, **_k: {}, LLMError=RuntimeError),
        "src.hooks.file_context": _module("src.hooks.file_context", inject_context=lambda *_a, **_k: None),
        "src.config": _module("src.config", config=cfg),
        "src.runtime.chat_orchestration_adapter": _module(
            "src.runtime.chat_orchestration_adapter",
            execute_chat_orchestration=_noop_async,
            execute_runtime_task_request=_noop_async,
        ),
        "src.runtime.runtime_task_tracker": _module("src.runtime.runtime_task_tracker", RuntimeTaskTracker=type("RTT", (), {})),
        "src.runtime.portal_session_metadata_client": _module(
            "src.runtime.portal_session_metadata_client",
            extract_session_metadata_publish_fields=lambda *_a, **_k: {},
            publish_session_metadata=_noop_async,
        ),
        "src.runtime.progressive_context": _module("src.runtime.progressive_context", build_portal_context_preview=lambda *_a, **_k: {}),
        "src.gateway.chat_payloads": _module(
            "src.gateway.chat_payloads",
            build_webchat_response_payload=lambda *_a, **_k: {},
            normalize_assistant_history_message=lambda x: x,
        ),
        "src.gateway.webchat_request_contracts": _module(
            "src.gateway.webchat_request_contracts",
            build_stream_start_event_payload=lambda *_a, **_k: {},
            extract_trusted_client_request_id=lambda *_a, **_k: None,
        ),
        "src.runtime.capability_registry": _module("src.runtime.capability_registry", get_capability_registry=lambda: {}),
        "src.gateway.event_bus": _module("src.gateway.event_bus", emit_agent_event=lambda *_a, **_k: None),
        "src.sessions.manager": _module(
            "src.sessions.manager",
            resolve_session_display_name=lambda *_a, **_k: "",
            session_manager=types.SimpleNamespace(
                get_context_state=_noop_async,
                get_active_skill_session=_noop_async,
            ),
        ),
        "src.sessions.persistence": _module("src.sessions.persistence", session_persistence=types.SimpleNamespace()),
        "src.sessions.usage": _module("src.sessions.usage", usage_tracker=types.SimpleNamespace()),
    }

    # load real github url utils for normalization contract
    gh_spec = importlib.util.spec_from_file_location("src.github.url_utils", Path("src/github/url_utils.py"))
    gh_mod = importlib.util.module_from_spec(gh_spec)
    assert gh_spec and gh_spec.loader
    gh_spec.loader.exec_module(gh_mod)
    modules["src.github.url_utils"] = gh_mod

    prev = {name: sys.modules.get(name) for name in modules}
    sys.modules.update(modules)
    src_pkg.gateway = gateway_pkg

    spec = importlib.util.spec_from_file_location("src.gateway.webchat", Path("src/gateway/webchat.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["src.gateway.webchat"] = module
    spec.loader.exec_module(module)

    def _cleanup():
        for name, old in prev.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        sys.modules.pop("src.gateway.webchat", None)

    return module, _cleanup
