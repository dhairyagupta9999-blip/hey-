#!/usr/bin/env python
"""Phase 31 §4.0 baseline harness — real-hardware measurement.

Replaces the hand-run benchmark_*.py scripts (§9.4: several shipped result files
as small as 352 bytes, i.e. not measurements).

For each fixed command it records:
  * wall_clock_ms      - end-to-end routing latency, stopwatch
  * tier_used          - which tier answered
  * remote_calls       - count of paid TypeSafe roundtrips (backend.decide)
  * reaches_remote     - whether control actually reaches the remote battery
  * resolved           - what the pipeline decided, if anything

TTS time-to-first-audio is recorded as UNAVAILABLE with a reason rather than
skipped, per the standing rule: a capability that is absent must never
masquerade as a pass.

Usage:
    python benchmarks/run_baseline.py [--out benchmarks/baseline_windows.json]
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# The command set fixed by §4.0.
COMMANDS = [
    "open calculator",
    "open spotify",
    "set volume to 50",
    "pause",
    "open youtube",
    "open youtube in chrome",
]

REPEATS = 3


def _stub_optional_audio_stack() -> list[str]:
    """Stub only modules the *routing* path never touches.

    This harness measures decision/routing latency from a text transcript.
    Microphone capture and neural STT are Phase 32 concerns (§7.1) and are not
    part of §4.0. Anything stubbed is listed in the output so the reader can see
    exactly what was real and what was stood in for.
    """
    stubbed: list[str] = []

    def stub(name: str, **attrs) -> None:
        if name in sys.modules:
            return
        try:
            __import__(name)
            return  # real module available — do not shadow it
        except ImportError:
            pass
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        stubbed.append(name)

    stub("keyboard", add_hotkey=lambda *a, **k: (lambda: None),
         add_listener=lambda *a, **k: None, wait=lambda *a, **k: None)
    stub("onnxruntime")
    stub("kokoro_onnx", Kokoro=object)
    stub("openwakeword", Model=object, models={})
    stub("openwakeword.model", Model=object)
    stub("faster_whisper", WhisperModel=object)
    return stubbed


def classify_tier(direct, res, reached_remote: bool) -> str:
    if not reached_remote:
        return "tier2_local"
    return "tier1_remote_battery"


def measure(command: str) -> dict:
    import siri
    from tier2_resolver import resolve_tier2

    result: dict = {"command": command}

    # --- Remote-call counter: wrap the real backend decide() --------------
    import backend as backend_mod
    calls = {"n": 0}

    real_decide = backend_mod.get_backend().decide

    def counting_decide(questions, utterance):
        calls["n"] += 1
        return real_decide(questions, utterance)

    backend_mod.get_backend().decide = counting_decide

    t0 = time.perf_counter()
    direct = siri.is_tier2_direct_match(command)
    res = None
    error = None
    try:
        res = resolve_tier2(command)
    except Exception as exc:                      # noqa: BLE001 - recorded, not swallowed
        error = f"{type(exc).__name__}: {exc}"
    wall_ms = (time.perf_counter() - t0) * 1000

    backend_mod.get_backend().decide = real_decide

    # --- Replicate siri.py:573-582 control flow exactly --------------------
    # The `return` lives inside `if t2_res is not None`. If the direct match
    # fires but resolve_tier2 yields nothing, control FALLS THROUGH to the
    # remote battery at L588 -- the fallthrough is the real cost driver.
    reached_remote = not (bool(direct) and res is not None)

    result.update({
        "is_tier2_direct_match": bool(direct),
        "resolve_status": (res or {}).get("status") if isinstance(res, dict) else None,
        "resolve_action": (res or {}).get("action") if isinstance(res, dict) else None,
        "resolved_app": (res or {}).get("app") if isinstance(res, dict) else None,
        "reaches_remote": reached_remote,
        "tier_used": classify_tier(direct, res, reached_remote),
        "remote_calls_observed": calls["n"],
        "wall_clock_ms": round(wall_ms, 2),
        "error": error,
    })
    return result


def run() -> dict:
    stubbed = _stub_optional_audio_stack()

    import app_index
    t0 = time.perf_counter()
    idx = app_index.get_app_index()
    index_build_ms = (time.perf_counter() - t0) * 1000
    index_entries = len(idx.apps)

    per_command = []
    for cmd in COMMANDS:
        runs = [measure(cmd) for _ in range(REPEATS)]
        lat = [r["wall_clock_ms"] for r in runs]
        first = runs[0]
        per_command.append({
            "command": cmd,
            "tier_used": first["tier_used"],
            "is_tier2_direct_match": first["is_tier2_direct_match"],
            "resolve_status": first["resolve_status"],
            "resolve_action": first["resolve_action"],
            "resolved_app": first["resolved_app"],
            "reaches_remote_battery": first["reaches_remote"],
            "remote_calls_observed": first["remote_calls_observed"],
            "wall_clock_ms_median": round(statistics.median(lat), 2),
            "wall_clock_ms_min": round(min(lat), 2),
            "wall_clock_ms_max": round(max(lat), 2),
            "samples": len(runs),
        })

    # --- What could NOT be measured here, stated rather than skipped ------
    unmeasured = []

    try:
        import backend as _b
        has_key = bool(getattr(_b.get_backend(), "api_key", None))
    except Exception:
        has_key = False
    if not has_key:
        unmeasured.append({
            "metric": "remote_battery_roundtrip_ms",
            "reason": "TYPESAFE_API_KEY not configured; the paid TypeSafe call "
                      "cannot be issued from this machine.",
            "affects": [c["command"] for c in per_command if c["reaches_remote_battery"]],
            "note": "The plan's ~1200 ms figure is therefore UNVERIFIED on this "
                    "hardware. Do not treat it as measured.",
        })

    unmeasured.append({
        "metric": "utterance_end_to_first_audible_ms",
        "reason": "Default TTS is Fish Audio (hosted, key-required) and no "
                  "microphone capture was used; STT is excluded by design "
                  "because §4.0 feeds text transcripts.",
        "affects": COMMANDS,
        "note": "Phase 32 (§7.1) owns this metric.",
    })

    return {
        "schema": "heyjev.baseline/1",
        "produced_by": "benchmarks/run_baseline.py",
        "phase": "31 §4.0",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "method": {
            "repeats_per_command": REPEATS,
            "statistic": "median of repeats",
            "input": "text transcripts (no microphone; STT excluded by design)",
            "remote_call_instrumentation":
                "backend.get_backend().decide is wrapped with a counter",
            "stubbed_modules": stubbed or ["(none — all audio modules importable)"],
        },
        "app_index": {"entries": index_entries,
                      "build_ms": round(index_build_ms, 2)},
        "commands": per_command,
        "not_measured": unmeasured,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "benchmarks" / "baseline_windows.json"))
    args = ap.parse_args()

    data = run()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {out}")
    print()
    hdr = (f"{'command':<26} {'tier':<20} {'remote':<8} "
           f"{'median_ms':>10}")
    print(hdr)
    print("-" * len(hdr))
    for c in data["commands"]:
        print(f"{c['command']:<26} {c['tier_used']:<20} "
              f"{str(c['reaches_remote_battery']):<8} {c['wall_clock_ms_median']:>10.2f}")
    print()
    for u in data["not_measured"]:
        print(f"NOT MEASURED: {u['metric']} -- {u['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())