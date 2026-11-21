from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_portal_trust_token(monkeypatch):
    """A test says itself whether the pod has a Portal trust token.

    An assistant pod has ``EFP_PORTAL_TRUST_TOKEN`` in its environment; a suite
    run there would otherwise stop believing every ``X-Portal-Author-Source``
    header the tests send.
    """

    monkeypatch.delenv("EFP_PORTAL_TRUST_TOKEN", raising=False)
