from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


import pytest


@pytest.fixture(autouse=True)
def _forget_shared_copilot_models_catalog():
    """The transport's catalog cache is per credential and process-wide; tests start clean."""

    from efp_runtime.llm.provider import clear_models_catalog_cache

    clear_models_catalog_cache()
    yield
    clear_models_catalog_cache()
