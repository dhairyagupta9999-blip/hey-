"""Behavioral test harness (§5.1).

Runs the REAL routing path from a text transcript and observes real state.
No mocks of the code under test: the modules exercised here are the genuine
`siri.is_tier2_direct_match`, `tier2_resolver.resolve_tier2`, and
`app_index.AppIndex`, loaded from the repository source.

Two things are deliberately stood in for, and both are named in
`HARNESS_LIMITS` so no reader mistakes them for real coverage:

  * The audio/vision stack (Whisper STT, Kokoro TTS, openWakeWord) is not
    installed in the test environment. §4.0 measures from text transcripts and
    explicitly excludes STT, so these modules are stubbed to permit import.
    Tests that genuinely need audio hardware xfail instead of skipping.

  * The paid TypeSafe remote battery cannot be called without a key. Rather
    than silently pretending it resolved, the harness counts *attempts* to
    reach it and aborts. `remote_calls > 0` therefore means "control reached
    the paid boundary" -- which is exactly the defect under test.

STUB HYGIENE
Stubs are installed by a session fixture, not at import time, and torn down
afterwards. An earlier version installed them at module level, which leaked
into `sys.modules` for every other test in the same interpreter and changed
their failure modes (a genuine `ModuleNotFoundError: faster_whisper` was being
reported as `TypeError: object() takes no arguments`). A stand-in must never
make a real problem look like a different real problem.

ISOLATION
Prefer running this tier on its own:  pytest tests/behavioral -q
The legacy root-level suite calls resolve_tier2("refresh apps"), which REBUILDS
the app_index singleton in-process. When both tiers share one interpreter, a
behavioral test observing the index can see state another test left behind --
an ambiguous or mid-rebuild catalog then surfaces as an intermittent launch
failure unrelated to the code under test. §5.4 already runs the tiers as
separate commands; keep it that way.
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

HARNESS_LIMITS = {
    "not_installed": ["faster_whisper", "kokoro_onnx", "openwakeword"],
    "not_exercised": [
        "microphone capture (audio_io)",
        "Whisper STT (excluded by §4.0 - text transcripts are the input)",
        "TTS playback (Phase 32 / §7.1 owns time-to-first-audio)",
        "paid TypeSafe remote battery (requires TYPESAFE_API_KEY)",
    ],
}

_MISSING = object()
_SHADOWED: dict[str, object] = {}


def _stub_missing_audio_modules() -> list[str]:
    """Install stand-ins only for modules that are genuinely absent."""
    stubbed: list[str] = []

    def stub(name: str, **attrs) -> None:
        if name in sys.modules:
            return
        try:
            __import__(name)
            return  # real module available - do not shadow it
        except ImportError:
            pass
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        _SHADOWED[name] = _MISSING
        sys.modules[name] = mod
        stubbed.append(name)

    stub("onnxruntime")
    stub("kokoro_onnx", Kokoro=object)
    stub("openwakeword", Model=object, models={})
    stub("openwakeword.model", Model=object)
    stub("faster_whisper", WhisperModel=object)
    return stubbed


def _restore_shadowed_modules() -> None:
    """Remove every stand-in this harness installed."""
    for name in list(_SHADOWED):
        sys.modules.pop(name, None)
    _SHADOWED.clear()


try:  # pytest is present in the intended environment
    import pytest as _pytest

    @_pytest.fixture(scope="session", autouse=True)
    def _audio_stack_stubs():
        """Install stubs for the behavioral tier only, then remove them."""
        installed = _stub_missing_audio_modules()
        try:
            yield installed
        finally:
            _restore_shadowed_modules()

except ImportError:  # harness imported outside pytest (e.g. the probe script)
    _stub_missing_audio_modules()


class RemoteBatteryReached(Exception):
    """Raised to abort the pipeline when control reaches the paid boundary."""


def process_names() -> set[str]:
    """Real process table, via psutil."""
    import psutil
    return {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]}


def wait_for_process(fragment: str, timeout: float = 3.0,
                     extra: set[str] | None = None) -> str | None:
    """Poll the REAL process table until `fragment` appears. Returns the name."""
    deadline = time.perf_counter() + timeout
    baseline = set(extra or ())
    while time.perf_counter() < deadline:
        for name in process_names():
            if fragment.lower() in name.lower() and name not in baseline:
                return name
        time.sleep(0.05)
    return None


class PipelineResult:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __repr__(self) -> str:
        return (f"PipelineResult(tier={self.tier!r} status={self.status!r} "
                f"remote_calls={self.remote_calls} "
                f"reaches_remote={self.reaches_remote} "
                f"elapsed_ms={self.elapsed_ms:.1f})")


def run_command_pipeline(text: str, execute: bool = True) -> PipelineResult:
    """Replicates siri.handle()'s control flow for a text utterance.

    Mirrors siri.py:573-582 exactly, including the detail that the `return`
    sits inside `if t2_res is not None` -- so a direct match that fails to
    resolve still falls through to the paid battery.

    execute=False times the DECISION path only, with no side effects. Use it
    when asserting on routing or latency; a latency bound measured while
    attempting a real launch of a missing app is testing the launcher.
    """
    import backend as backend_mod
    import siri
    from tier2_resolver import resolve_tier2

    remote_calls = {"n": 0}
    real_backend = backend_mod.get_backend()

    def counting_decide(questions, utterance):
        remote_calls["n"] += 1
        raise RemoteBatteryReached(
            "control reached backend.decide() -- a paid remote roundtrip")

    real_decide = real_backend.decide
    real_backend.decide = counting_decide

    started = time.perf_counter()
    direct = bool(siri.is_tier2_direct_match(text))
    status = action = app = None
    error = None

    try:
        if direct:
            if execute:
                res = resolve_tier2(text)
                if res is not None:
                    status = res.get("status")
                    action = res.get("action")
                    app = res.get("app")
                reached_remote = res is None
            else:
                reached_remote = False
        else:
            # Branch 1 did not match: the real pipeline now issues a PAID
            # backend.decide() call. Invoke the instrumented stub so the
            # attempt is actually counted -- otherwise `remote_calls` would be
            # trivially 0 whenever routing fails, and every "no remote call"
            # assertion would pass vacuously.
            real_backend.decide({}, text)
            reached_remote = True
    except RemoteBatteryReached as exc:
        reached_remote = True
        error = str(exc)
    except Exception as exc:                       # noqa: BLE001 - surfaced to the test
        reached_remote = not direct
        error = f"{type(exc).__name__}: {exc}"
    finally:
        real_backend.decide = real_decide

    elapsed_ms = (time.perf_counter() - started) * 1000

    return PipelineResult(
        command=text,
        tier="tier2_local" if not reached_remote else "tier1_remote_battery",
        status=status,
        action=action,
        app=app,
        remote_calls=remote_calls["n"],
        reaches_remote=reached_remote,
        elapsed_ms=elapsed_ms,
        error=error,
    )


def app_is_installed(query: str) -> bool:
    """True when the LOCAL app index can resolve `query` with high confidence."""
    import app_index
    entry, score, amb = app_index.get_app_index().find_app(query)
    return bool(entry and score >= 70.0 and not amb)