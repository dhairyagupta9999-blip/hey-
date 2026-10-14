"""Session-wide isolation for module-level global state.

This repo carries several process-wide singletons that tests mutate and never
restore. The one that caused real, confusing failures:

  safety_engine._CANCEL_EVENT is set by
  `request_global_cancel()` and is global for the process. `test_phase6.py`
  calls it and never clears it, so every LATER test in the same interpreter
  sees `is_cancel_requested() == True`. `mcp_client.call_tool()` consults that
  flag, so the Windows-MCP integration tests all failed with "cancelled" --
  while passing perfectly in isolation. A test failing only in the full suite,
  for a reason three files away, is the worst kind of failure to debug.

These fixtures restore global state around every test. They do not make tests
pass that should fail; they stop one test's leftovers from deciding another's
outcome.

Note this is the SAME class of defect as the app_index singleton rebuild
documented in tests/behavioral/conftest.py: shared global state across a
combined run. That one is why the behavioral tier runs as its own invocation;
this one is cheap enough to fix properly.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _reset_global_cancel_state():
    """Clear safety_engine's process-wide cancel flag around every test."""
    try:
        import safety_engine
    except Exception:
        yield
        return

    check_and_clear = getattr(safety_engine, "check_and_clear_cancel", None)
    if check_and_clear is not None:
        check_and_clear()
    try:
        yield
    finally:
        if check_and_clear is not None:
            check_and_clear()


@pytest.fixture(autouse=True)
def _reset_confirmation_manager():
    """Drop any pending HIGH-risk confirmation left behind by another test.

    A pending confirmation makes `handle()` divert into the confirmation
    branch instead of routing the utterance, which silently changes what later
    tests exercise.
    """
    try:
        import safety_engine
    except Exception:
        yield
        return

    try:
        manager = safety_engine.get_confirmation_manager()
    except Exception:
        yield
        return

    try:
        if manager.has_pending():
            manager.cancel_pending("test isolation reset")
        yield
    finally:
        try:
            if manager.has_pending():
                manager.cancel_pending("test isolation reset")
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _restore_heyjev_env():
    """Restore HEYJEV_* environment variables a test may have set."""
    saved = {k: v for k, v in os.environ.items() if k.startswith("HEYJEV_")}
    try:
        yield
    finally:
        for key in [k for k in os.environ if k.startswith("HEYJEV_")]:
            if key not in saved:
                os.environ.pop(key, None)
        os.environ.update(saved)