"""B1 — routine app launches must not pay a paid remote roundtrip.

Re-targeted after the §4.0 baseline falsified the original framing.

The plan's original claim was that app names *missing* from apps.json
(e.g. "calculator") fell through to the remote battery. Measured on real
hardware, that is false: `is_tier2_direct_match` returns True for them via its
fifth branch ("Open/close/focus with a target not in APPS"), and they resolve
locally in single-digit milliseconds.

The defect that is real is the mirror image. Apps that ARE in APPS take branch
1, which unconditionally returns False to "defer to the remote battery" --
without ever asking the local index, which resolves them at score 100.0. So
"open spotify" pays a hosted api.typesafe.ai roundtrip for an answer the
machine already has.

These tests assert the real behaviour, and each one fails on pre-fix code.
"""
from __future__ import annotations

import pytest

from conftest import (  # noqa: F401  (conftest is loaded as a plugin)
    app_is_installed,
    process_names,
    run_command_pipeline,
    wait_for_process,
)

# Apps that appear in siri.APPS (the fixed battery list) and therefore take
# branch 1 -- the deferral that B1 removes.
BATTERY_APPS = ["spotify", "slack", "zoom"]


@pytest.mark.parametrize("app_name", BATTERY_APPS)
def test_battery_app_resolves_without_remote_call(app_name):
    """An app in the fixed battery list must resolve locally, with no network.

    This is the B1 assertion. On pre-fix code control reaches backend.decide(),
    a paid api.typesafe.ai roundtrip, and `remote_calls` is 1.
    """
    pytest.importorskip("app_index")
    if not app_is_installed(app_name):
        pytest.xfail(f"local app index cannot resolve {app_name!r} on this machine")

    result = run_command_pipeline(f"open {app_name}", execute=False)

    assert result.remote_calls == 0, (
        f"{app_name!r} paid a remote roundtrip for a local answer "
        f"(tier={result.tier}, status={result.status})"
    )
    assert result.reaches_remote is False, (
        f"control fell through to the paid battery: {result!r}"
    )


def test_open_spotify_is_local_and_fast():
    """`open spotify` must resolve locally well inside the Phase 32 budget.

    Measured with execute=False on purpose: B1 changed the *decision* path, so
    the latency bound must time the decision and not an attempted launch of an
    app that may not be installed. That is the difference between testing the
    fix and testing the launcher. The full end-to-end launch is covered
    separately by the calculator test below.
    """
    if not app_is_installed("spotify"):
        pytest.xfail("spotify is not resolvable from the local index here")

    result = run_command_pipeline("open spotify", execute=False)

    assert result.remote_calls == 0, f"paid remote roundtrip: {result!r}"
    assert result.elapsed_ms < 1000, (
        f"{result.elapsed_ms:.0f} ms exceeds the 1000 ms local routing budget: "
        f"{result!r}"
    )


def test_calculator_launch_is_local_and_starts_the_process():
    """End-to-end: 'open calculator' resolves locally AND starts a real process.

    This is the plan's original §5.3 test 1, corrected. The process assertion
    is genuine -- it watches the real process table -- and xfails if Calculator
    is not installed, never silently skips.
    """
    if not app_is_installed("calculator"):
        pytest.xfail("calculator is not resolvable from the local index here")

    # Make the launch observable regardless of test ordering: a Calculator left
    # running by an earlier test would otherwise be indistinguishable from one
    # this command started.
    _kill_calculator()

    before = process_names()
    result = run_command_pipeline("open calculator")
    try:
        assert result.remote_calls == 0, f"paid remote roundtrip: {result!r}"

        # Assert the routing contract first: it is fast and deterministic, and
        # it localises the failure if the process observation below goes wrong.
        # The legacy suite calls resolve_tier2("refresh apps"), which rebuilds
        # the app_index singleton; asserting the status makes that cross-test
        # state visible instead of surfacing as an opaque launch timeout.
        assert result.status == "done", (
            f"'open calculator' did not resolve cleanly: {result!r} "
            f"(status={result.status!r} action={result.action!r} "
            f"error={result.error!r})"
        )

        launched = wait_for_process("calculator", timeout=15.0, extra=before)
        if launched is None:
            pytest.fail(
                "calculator process never appeared despite status='done'; the "
                "launcher reported success without starting a process "
                "(PROJECT_VISION.md: never report success unless verified)"
            )

        assert result.elapsed_ms < 5000
    finally:
        _kill_calculator()


def _kill_calculator() -> None:
    """Terminate any Calculator process AND wait for it to actually exit.

    Best effort, but the wait matters: killing a Calculator and immediately
    relaunching it races Windows' process teardown, and the relaunch can
    report success while starting nothing. Observed as an intermittent failure
    of this file when run in the same interpreter as the legacy suite.
    """
    import time

    import psutil

    for proc in psutil.process_iter(["name"]):
        name = proc.info["name"] or ""
        if "alculator" in name.lower():
            try:
                proc.kill()
            except Exception:
                pass

    deadline = time.perf_counter() + 10.0
    while time.perf_counter() < deadline:
        if not any(
            "alculator" in (p.info["name"] or "").lower()
            for p in psutil.process_iter(["name"])
        ):
            return
        time.sleep(0.1)