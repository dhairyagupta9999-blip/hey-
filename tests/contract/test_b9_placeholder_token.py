"""B9 — the MCP gateway must refuse to start on the shipped placeholder token.

`.env.example` ships `JEV_OPENDOTS_BEARER_TOKEN=change_me_to_a_secure_random_token`.
validate() already rejects an *empty* token, but a token that is literally the
documented placeholder passes every existing check: it is non-empty, and
`authenticate_token` will constant-time-compare happily against it. A user who
copies .env.example to .env and enables the gateway gets an unauthenticated-
in-practice local service with no error at all.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import pytest


def _placeholder() -> str:
    from config import OPENDOTS_TOKEN_PLACEHOLDER
    return OPENDOTS_TOKEN_PLACEHOLDER


def test_placeholder_is_plain_ascii():
    """The sentinel must be ASCII and non-empty.

    §1.3 withdrew v1's claim that this default contains a non-ASCII U+2011.
    The value really is ASCII; this test exists to keep it that way rather than
    to confirm a defect that was never there.
    """
    value = _placeholder()
    assert value, "placeholder must be a non-empty sentinel"
    assert value.isascii(), f"placeholder must be plain ASCII: {value!r}"
    assert len(value) >= 16, "placeholder should be unmistakably a placeholder"


def test_gateway_refuses_placeholder_token():
    """Enabling the gateway with the shipped placeholder must raise."""
    from opendots_gateway import OpenDotsGatewayConfig

    cfg = OpenDotsGatewayConfig(
        enabled=True, bearer_token=_placeholder(), host="127.0.0.1", port=8765
    )
    with pytest.raises(ValueError, match="placeholder"):
        cfg.validate()


def test_gateway_refuses_placeholder_case_and_space_variants():
    """Trivial formatting must not smuggle the placeholder through."""
    from opendots_gateway import OpenDotsGatewayConfig

    for variant in (_placeholder().upper(), f"  {_placeholder()}  "):
        cfg = OpenDotsGatewayConfig(
            enabled=True, bearer_token=variant, host="127.0.0.1", port=8765
        )
        with pytest.raises(ValueError, match="placeholder"):
            cfg.validate()


def test_gateway_still_accepts_a_real_token():
    """Guard against over-correction: a genuine token must still start."""
    from opendots_gateway import OpenDotsGatewayConfig

    cfg = OpenDotsGatewayConfig(
        enabled=True,
        bearer_token="k3f9a2b7c1d4e6f8a0b3c5d7e9f1a2b4c6d8e0f1a3b5c7d9e1f3a5b7c9d1e3f5",
        host="127.0.0.1",
        port=8765,
    )
    cfg.validate()  # must not raise


def test_env_example_ships_the_shared_sentinel():
    """.env.example and the code must agree on one placeholder value."""
    from pathlib import Path

    import config as config_mod

    repo = Path(config_mod.REPO_ROOT)
    text = (repo / ".env.example").read_text(encoding="utf-8")
    assert f"JEV_OPENDOTS_BEARER_TOKEN={_placeholder()}" in text, (
        ".env.example placeholder has drifted from config.OPENDOTS_TOKEN_PLACEHOLDER"
    )