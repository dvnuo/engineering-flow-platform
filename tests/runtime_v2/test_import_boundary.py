import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_v2_imports_standalone_with_pythonpath_src():
    code = """
import json
import sys

import efp_runtime
import efp_runtime.llm.adapter
import efp_runtime.runtime
import efp_runtime.session.processor
import efp_runtime.tools.runtime

print(json.dumps({"legacy_core_loaded": "src.agents.core" in sys.modules}))
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
    assert payload == {"legacy_core_loaded": False}


def test_runtime_v2_source_does_not_import_through_src_package():
    combined = _combined_v2_source()
    assert "from src.efp_runtime" not in combined
    assert "import src.efp_runtime" not in combined


def test_runtime_v2_source_has_no_direct_src_imports():
    offenders: list[str] = []
    for path in sorted((ROOT / "src/efp_runtime").rglob("*.py")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith(("from src.", "import src.")):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}"
                )

    assert offenders == []


def test_runtime_v2_source_does_not_import_legacy_runtime_modules():
    combined = _combined_v2_source()
    forbidden_imports = [
        "from src.agents.core",
        "import src.agents.core",
        "from src.agents.skill_runtime",
        "import src.agents.skill_runtime",
        "from src.agents.skill_mode",
        "import src.agents.skill_mode",
        "from src.skills",
        "import src.skills",
        "from src.agents.tool_result_policy",
        "import src.agents.tool_result_policy",
        "Agent.process(",
        "SkillSession(",
        "SkillsExecutor(",
        "src.bash_tools",
        "src.github",
        "src.jira",
        "src.confluence",
        "src.git",
        "src.context_tools",
    ]
    for token in forbidden_imports:
        assert token not in combined


def _combined_v2_source() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((ROOT / "src/efp_runtime").rglob("*.py"))
    )
