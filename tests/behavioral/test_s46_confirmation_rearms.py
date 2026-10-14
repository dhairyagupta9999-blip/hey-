"""§4.6 — the confirmation timeout is hostile for speech.

`_CONFIRM_TIMEOUT_S = 8.0` and `request_high_risk_confirmation(timeout_s=8.0)`.
Fail-safe-by-timeout is correct in principle: an unanswered prompt must default
to NO. But a human cannot reliably answer "I am about to close all windows,
should I proceed?" in eight seconds. The realistic outcome is that a correct
user is treated identically to someone who ignored the question, and the
dangerous action never happens while the person stands there waiting.

The fix raises the default to 20 seconds and allows ONE re-arm: if the user
speaks a hesitation ("wait", "are you sure", "hold on") the prompt is
re-issued and the timer restarts. One re-arm only -- an unbounded loop would
let a hesitant user postpone a high-risk action indefinitely, which is its own
failure mode.

Fail-safe is preserved throughout: a timeout still defaults to NO.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def manager():
    import safety_engine
    cm = safety_engine.get_confirmation_manager()
    cm.cancel_pending("test setup")
    yield cm
    cm.cancel_pending("test teardown")


def test_default_timeout_is_20_seconds():
    """8 seconds is below the floor for a spoken answer."""
    import inspect

    import safety_engine

    src = inspect.signature(
        safety_engine.ConfirmationManager.request_high_risk_confirmation
    )
    default = src.parameters["timeout_s"].default
    assert default >= 20.0, (
        f"default confirmation timeout is {default}s; a spoken answer to a "
        "high-risk prompt needs at least 20s"
    )


def test_mcp_confirmation_sites_do_not_override_the_timeout():
    """Behavioural, not a source grep.

    An earlier version of this file used `inspect.getsource(respond)` and
    asserted the literal '"timeout_s", 8.0' was absent -- the exact
    source-assertion pattern Phase 31 deleted from the suite. Worse, it could
    not catch the real defect: every MCP confirmation site passed
    `timeout_s=8.0` EXPLICITLY, so the 20 s default and its hesitation re-arm
    never applied to UI automation at all, while the grep stayed green.

    This asserts the observable behaviour instead: a confirmation requested
    with the module default must survive well past eight seconds.
    """
    import inspect

    import safety_engine

    manager = safety_engine.get_confirmation_manager()
    executed = []
    default_timeout = inspect.signature(
        safety_engine.ConfirmationManager.request_high_risk_confirmation
    ).parameters["timeout_s"].default

    assert default_timeout >= 20.0, (
        f"default is {default_timeout}s; §4.6 requires >= 20s for a spoken answer"
    )

    # Ask for confirmation WITHOUT passing timeout_s, exactly as the MCP sites
    # now do, and confirm it is still pending after 9 s -- which is beyond the
    # old 8 s default and would previously have expired.
    manager.request_high_risk_confirmation(
        "ui_type", "I am about to type text into the focused window.",
        lambda: executed.append(True),
    )
    pending = manager.pending_confirmation
    assert pending is not None
    assert pending["timeout_s"] == default_timeout, (
        f"stored timeout {pending['timeout_s']} != module default {default_timeout}; "
        "a call site is still overriding it"
    )
    assert pending["timeout_s"] > 8.0, (
        "the stored timeout is still the old 8 s default"
    )
    manager.cancel_pending("test cleanup")


def test_hesitation_rearms_the_prompt(manager):
    """A user who says 'wait' must get the prompt again, not a silent timeout."""
    executed = []
    manager.request_high_risk_confirmation(
        "close_all",
        "I am about to close all windows. Should I proceed?",
        lambda: executed.append(True),
    )

    handled, message = manager.respond("wait, are you sure?")

    assert handled, f"hesitation was not recognised as a response: {message!r}"
    assert not executed, "action executed on a hesitation"
    assert manager.pending_confirmation is not None, (
        "hesitation cleared the pending confirmation instead of re-arming it"
    )


def test_rearm_is_bounded_to_one(manager):
    """One re-arm only, so hesitation cannot postpone an action forever."""
    for _ in range(4):
        manager.respond("wait")

    handled, message = manager.respond("wait")
    assert not handled, (
        "the re-arm appears to be unbounded; a hesitant user could postpone a "
        f"high-risk action indefinitely ({message!r})"
    )


def test_timeout_still_defaults_to_no(manager):
    """Fail-safe must survive the fix: silence never means yes."""
    executed = []
    manager.request_high_risk_confirmation(
        "delete_all",
        "This is irreversible.",
        lambda: executed.append(True),
        timeout_s=0.05,
    )
    import time
    time.sleep(0.4)

    assert not executed, "a timed-out confirmation executed the action"
    assert manager.pending_confirmation is None or not manager.pending_confirmation.get(
        "state", {}
    ).get("confirmed"), "confirmation remained affirmative after timeout"


def test_explicit_yes_still_executes(manager):
    """Guard against over-correction."""
    executed = []
    manager.request_high_risk_confirmation(
        "close_all",
        "Should I proceed?",
        lambda: executed.append(True),
        timeout_s=30.0,
    )
    handled, message = manager.respond("yes")

    assert handled, f"an explicit yes was rejected: {message!r}"
    assert executed, "an explicit yes did not execute the action"