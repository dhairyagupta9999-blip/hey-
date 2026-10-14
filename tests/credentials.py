"""Shared test helpers for credential-gated tests.

Several tests exercise code paths that construct a real provider client before
their mocks take effect -- `JevBackend.__init__` resolves a TypeSafe key, the
answer provider walks its fallback chain. On a machine with credentials they
pass. On CI, which has none, they fail for a reason that has nothing to do with
the code under test.

That distinction matters. A failing test here says "this machine has no API
key", not "Hey Jev is broken". Reporting it as a failure is noise that trains
people to ignore red.

These helpers mark such tests XFAIL with the missing credential named. Rules:
  * XFAIL, never skip -- a skipped test is invisible, an xfail is visible and
    self-describing.
  * The test still RUNS wherever credentials exist, so it is never silently
    turned off.
  * The reason states exactly which key is missing.
"""
from __future__ import annotations

import os
from typing import Optional

import pytest


def _secret(name: str) -> Optional[str]:
    """Read a credential from the environment or the OS credential store."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        import keyring
        return keyring.get_password("HeyJev", name)
    except Exception:
        return None


# Every credential any provider-gated test might need. A test passes if the
# specific one it names is present, OR if any provider credential exists at all
# (provider-selection tests genuinely only need one route to be live).
PROVIDER_KEYS = (
    "TYPESAFE_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "FISH_AUDIO_API_KEY",
)


def has_credential(*names: str) -> bool:
    return any(_secret(n) for n in names)


def has_any_provider_credential() -> bool:
    return has_credential(*PROVIDER_KEYS)


def requires_credential(*names: str):
    """xfail a test unless one of `names` is configured.

    Usage:
        @requires_credential("TYPESAFE_API_KEY")
        def test_something(self): ...
    """
    label = " or ".join(names)

    def decorator(func):
        if has_credential(*names):
            return func
        reason = (
            f"no credential configured ({label}); this test exercises a live "
            f"provider path. It runs wherever that key is present."
        )
        return pytest.mark.xfail(reason=reason, strict=False)(func)

    return decorator


def requires_any_provider_credential():
    """xfail unless at least one provider key exists."""
    def decorator(func):
        if has_any_provider_credential():
            return func
        reason = (
            "no provider credential configured (" + ", ".join(PROVIDER_KEYS) +
            "); provider-selection tests need one live route."
        )
        return pytest.mark.xfail(reason=reason, strict=False)(func)

    return decorator