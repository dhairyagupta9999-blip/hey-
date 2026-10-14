"""No paid API key should not mean a broken assistant.

Today the fallback is one-directional: `LayaBackend` falls back to the hosted
Jev backend when it fails to load, but nothing falls back *to* Laya. So a user
who clones this repo, installs the requirements, and does not have a
`TYPESAFE_API_KEY` gets:

    [auth error]: Invalid TypeSafe API key (401). Check your keys in .env or Settings.

and then silence. Meanwhile `backend.py` has carried a complete, Apache-2.0,
local, Jev-compatible decision backend since Phase 6, unused.

This makes the fallback bidirectional. Laya is non-generative and emits
calibrated probabilities, so it can stand in for the battery on exactly the
terms the project already documents: it cannot invent an action.

These tests fail on pre-fix code.
"""
from __future__ import annotations

import re
import time

import pytest


class _MissingKeyError(Exception):
    """Stands in for the HTTPError TypeSafe raises with no API key."""


def test_fallback_backend_is_available():
    """The composition helper must exist."""
    import backend as backend_mod
    assert hasattr(backend_mod, "get_backend_with_fallback"), (
        "backend.get_backend_with_fallback() is missing; a user without a "
        "TypeSafe key currently gets nothing but an auth error"
    )


def test_returns_primary_when_it_works():
    """When the hosted backend works, it is used unchanged."""
    import backend as backend_mod

    class _Primary:
        name = "jev"
        default_gate = 0.65

        def __init__(self):
            self.calls = 0

        def decide(self, questions, utterance):
            self.calls += 1
            return {"category": ("mac_command", 0.9)}, 12, 0.00004

    primary = _Primary()
    composed = backend_mod.get_backend_with_fallback(primary, fallback_to=None)

    ans, ms, cost = composed.decide({}, "open calculator")

    assert primary.calls == 1
    assert ans["category"][0] == "mac_command"
    assert cost == 0.00004, "cost must be reported unchanged when the paid path is used"


def test_falls_back_to_local_on_missing_key():
    """The headline case: no TypeSafe key, but the assistant still answers."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("Missing TypeSafe API key")

    class _Local:
        name = "laya"
        default_gate = 0.45

        def __init__(self):
            self.calls = 0

        def decide(self, questions, utterance):
            self.calls += 1
            return {"category": ("mac_command", 0.88)}, 140, 0.0

    local = _Local()
    composed = backend_mod.get_backend_with_fallback(_NoKey(), fallback_to=local)

    ans, ms, cost = composed.decide({}, "open calculator")

    # 2 calls: one latency probe + the real turn (see the latency-guard tests).
    assert local.calls == 2, (
        f"the local backend was not consulted (calls={local.calls}); expected a "
        "probe plus the real turn"
    )
    assert ans["category"][0] == "mac_command"
    assert cost == 0.0, "a local fallback must not report a per-turn cost"


def test_fallback_is_reported_not_silent():
    """A silent downgrade is the failure mode this project cares about.

    If the assistant quietly starts using a different engine, traces and cost
    accounting must say so. PROJECT_VISION: never report success unless
    verified.
    """
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    class _Local:
        name = "laya"
        default_gate = 0.45

        def decide(self, questions, utterance):
            return {}, 5, 0.0

    composed = backend_mod.get_backend_with_fallback(_NoKey(), fallback_to=_Local())
    # The switch happens on first failure, so drive a turn before asserting --
    # pre-switch the composite still legitimately reports the primary's name.
    composed.decide({}, "open calculator")
    assert "laya" in composed.name.lower(), (
        f"the composite reports name={composed.name!r} after falling back; a "
        "silent engine downgrade must be visible in the name and traces"
    )


def test_gate_follows_the_active_backend():
    """Laya uses 0.45, Jev 0.65. Using the wrong one either over- or under-fires."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    class _Local:
        name = "laya"
        default_gate = 0.45

        def decide(self, questions, utterance):
            return {}, 5, 0.0

    composed = backend_mod.get_backend_with_fallback(_NoKey(), fallback_to=_Local())
    composed.decide({}, "open calculator")
    assert composed.default_gate == 0.45, (
        f"gate is {composed.default_gate}; it must come from the backend that "
        "actually answered, or the confidence threshold is wrong"
    )


def test_no_fallback_configured_means_real_error():
    """With no local backend available the original error must surface."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    composed = backend_mod.get_backend_with_fallback(_NoKey(), fallback_to=None)

    with pytest.raises(_MissingKeyError):
        composed.decide({}, "open calculator")


# --------------------------------------------------------------------------
# Latency guard
#
# Measured on hardware without AVX2 (Intel i3-2350M): a Laya forward pass
# takes 36-190 s. Unconditionally falling back would replace a clear error with
# a two-minute freeze, which is worse -- the user cannot distinguish a slow
# machine from a broken one.
# --------------------------------------------------------------------------

class _SlowLocal:
    name = "laya"
    default_gate = 0.45

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.calls = 0

    def decide(self, questions, utterance):
        import time
        self.calls += 1
        time.sleep(min(self.seconds, 1.0))   # capped so the test stays fast
        return {}, 5, 0.0


def test_avx2_detection_is_correct_for_this_cpu():
    """The AVX2 pre-check must agree with reality, and must not fail open.

    An earlier ctypes-based version reported True on an i3-2350M (Sandy
    Bridge, no AVX2) because `__cpuid` is not reliably callable through windll
    with the right calling convention. It then failed OPEN -- the guard let the
    useless fallback through. A second version used `re` without importing it,
    raised NameError, and the bare `except` again returned True. Both were
    silent wrong answers, which is worse than no check at all.
    """
    import backend as backend_mod

    assert backend_mod._cpu_has_avx2() is True or \
           backend_mod._cpu_has_avx2() is False, (
        "_cpu_has_avx2 must return a real bool, never a truthy sentinel"
    )

    # On this machine (Intel64 Family 6 Model 42 = Sandy Bridge) it must be
    # False. Guarded so the assertion is skipped rather than wrong on modern
    # hardware, where True is the correct answer.
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        )
        try:
            identifier, _ = winreg.QueryValueEx(key, "Identifier")
        finally:
            winreg.CloseKey(key)
    except Exception:
        pytest.skip("cannot read the CPU identifier on this machine")

    match = re.search(r"Family\s+(\d+)\s+Model\s+(\d+)", str(identifier))
    if not match:
        pytest.skip(f"unrecognised CPU identifier format: {identifier!r}")

    family, model = int(match.group(1)), int(match.group(2))
    if "Intel" not in str(identifier) or family != 6:
        pytest.skip(f"not an Intel Family 6 part: {identifier!r}")

    if model < 60:
        # Pre-Haswell: must report False. This is the branch that was wrong.
        assert backend_mod._cpu_has_avx2() is False, (
            f"{identifier!r} is pre-Haswell and has no AVX2, but the check "
            "reported support -- the latency guard would be bypassed"
        )
    else:
        assert backend_mod._cpu_has_avx2() is True


def test_unsupported_cpu_refuses_without_running_the_probe():
    """A pre-Haswell CPU must be rejected INSTANTLY, not after a 60 s probe.

    Uses a real LayaBackend-shaped object via the actual class so the type
    check applies; the decide() is stubbed so no weights are loaded.
    """
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    if backend_mod._cpu_has_avx2():
        pytest.skip("this CPU supports AVX2; the pre-check cannot apply")

    class _StubLaya(backend_mod.LayaBackend):
        """A real LayaBackend that must never be asked to decide."""
        def __init__(self):
            super().__init__(fallback_to_jev=False)
            self.calls = 0

        def decide(self, questions, utterance):
            self.calls += 1
            raise AssertionError("the probe must not run on an unsupported CPU")

    local = _StubLaya()
    composed = backend_mod.FallbackBackend(_NoKey(), local, probe_budget_s=5.0)

    started = time.time()
    with pytest.raises(backend_mod.FallbackSlowError) as excinfo:
        composed.decide({}, "open calculator")
    elapsed = time.time() - started

    assert local.calls == 0, "the expensive probe ran despite a known-bad CPU"
    assert elapsed < 1.0, f"refusal took {elapsed:.2f}s; it must be immediate"
    assert "avx2" in str(excinfo.value).lower(), (
        f"the refusal must name the hardware reason: {excinfo.value}"
    )


def test_probe_uses_real_questions_not_an_empty_set():
    """The regression that made the latency guard dead code.

    laya's agent short-circuits BEFORE inference when handed an empty question
    dict, returning in ~0 ms. The original probe called decide({}, "hello"), so
    the guard always reported "viable" -- on hardware where a real turn takes
    two minutes. It looked like it worked, because it returned True.

    This pins the fix: the probe question set must be non-empty.
    """
    import backend as backend_mod

    assert backend_mod._PROBE_QUESTIONS, "probe question set must not be empty"
    assert list(backend_mod._PROBE_QUESTIONS), "probe question set must not be empty"

    for qid, spec in backend_mod._PROBE_QUESTIONS.items():
        assert spec.get("type") and spec.get("criteria"), (
            f"probe question {qid!r} is malformed: {spec!r}"
        )


def test_probe_failure_is_not_reported_as_too_slow():
    """An ImportError must never be dressed up as a slow machine.

    Both probe outcomes used to set the same boolean, so an offline hub or a
    bad checkpoint was reported to the user as "your machine is too slow" --
    sending them to fix the wrong problem.
    """
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    class _BrokenLocal:
        name = "laya"
        default_gate = 0.45

        def decide(self, questions, utterance):
            raise ConnectionError("huggingface.co unreachable")

    composed = backend_mod.FallbackBackend(_NoKey(), _BrokenLocal(), probe_budget_s=5.0)

    with pytest.raises(backend_mod.FallbackSlowError) as excinfo:
        composed.decide({}, "open calculator")

    message = str(excinfo.value).lower()
    assert "connectionerror" in message, (
        f"the real probe error was swallowed: {excinfo.value}"
    )
    assert "unreachable" in message, (
        f"the underlying cause must survive into the user-facing message: {excinfo.value}"
    )


def test_slow_fallback_is_refused_rather_than_hanging():
    """A local model slower than the budget must not be used."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    slow = _SlowLocal(seconds=30.0)
    composed = backend_mod.FallbackBackend(_NoKey(), slow, probe_budget_s=0.05)

    with pytest.raises(backend_mod.FallbackSlowError) as excinfo:
        composed.decide({}, "open calculator")

    message = str(excinfo.value).lower()
    assert "over the" in message and "budget" in message, (
        f"the refusal must name the measured time and the budget it exceeded, "
        f"not just fail: {excinfo.value}"
    )
    assert "no decision was made" in message, (
        "the refusal must state that nothing happened, so the user is not left "
        "wondering whether the assistant acted"
    )


def test_fast_fallback_is_still_used():
    """Guard against over-correction: a quick local model must still work."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    fast = _SlowLocal(seconds=0.01)
    composed = backend_mod.FallbackBackend(_NoKey(), fast, probe_budget_s=5.0)

    composed.decide({}, "open calculator")

    assert fast.calls == 2, "expected a probe call plus the real call"
    assert composed.switched_reason is not None


def test_probe_happens_once_not_per_turn():
    """Probing every turn would double the cost of every conversation."""
    import backend as backend_mod

    class _NoKey:
        name = "jev"
        default_gate = 0.65

        def decide(self, questions, utterance):
            raise _MissingKeyError("no key")

    fast = _SlowLocal(seconds=0.01)
    composed = backend_mod.FallbackBackend(_NoKey(), fast, probe_budget_s=5.0)

    for _ in range(5):
        composed.decide({}, "open calculator")

    assert fast.calls == 6, (
        f"expected 1 probe + 5 real calls, got {fast.calls}"
    )