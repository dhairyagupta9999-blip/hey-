# Hey Jev — session handoff

> Copy everything below this line into a new session.

---

Continue remediating the Hey Jev project. A verified plan and substantial
completed work exist — read this before touching code.

## Source of truth

- Plan: `C:\Users\Dell\.opencode\plan\hey-jev-remediation.md`
- My own findings: `docs\PHASE31_FINDINGS.md`, `docs\PRODUCTION_READINESS.md`,
  `docs\OPEN_SOURCE_RESEARCH.md`, `docs\LAYA_MEASUREMENT.md` (in the repo)

## Repo

- Local clone: `C:\Users\Dell\OneDrive\Documents\Default Project\hey-`
- Upstream: `https://github.com/dhairyagupta9999-blip/hey-`
  (name has a TRAILING HYPHEN; "hey" 404s). **Not yet pushed.**
- Windows 10/11 voice assistant, port of `henryklunaris/hey-jev` (MIT).
- 228 Python files, ~69,800 LOC, flat root layout.

## Environment

- Python 3.12.4. Venv already built: `C:\Users\Dell\AppData\Local\Temp\opencode\heyvenv\Scripts\python.exe`
- Git **is now installed** (2.55.0, `C:\Program Files\Git\cmd`) — needed by one test.
- `PySide6`, `faster_whisper`, `kokoro-onnx`, `laya 0.4.7`, `send2trash`,
  `rapidfuzz` all installed in that venv.
- Speech models already downloaded to `%APPDATA%\HeyJev\models`
  (Kokoro int8 108.8MB, voices 26.9MB, silero_vad 2.2MB).
- MCP binary downloaded to `mcp\Sbroenne.WindowsMcp.exe` (57.1MB, gitignored).

## Current verified state

```
842 passed, 0 failed, 6 xfailed     (pytest . --ignore=test_opencode_zen.py
                                     --ignore=test_phase28.py --ignore=test_phase3.py)
preflight.py: 14 pass, 0 fail, 1 warn
```

The 6 xfails are honest and self-describing: 4 need an external OpenDots
checkout at `C:\Users\prabh\...`, 2 need a provider credential.

Run tests from the repo root. **`tests/behavioral` must run as its own pytest
invocation** — the legacy suite calls `resolve_tier2("refresh apps")`, which
rebuilds the `app_index` singleton in-process and makes behavioral tests flake.

## Work already completed

Phase 31 correctness: **B1–B10 closed**, each with a test observed failing
pre-fix. Plus §4.6 confirmation timeout, `LICENSE` (MIT), CI workflow,
`requirements.txt` corrections (`rapidfuzz`/`send2trash`/`pywin32` were
imported but undeclared — a clean install could not run), source-assertion
tests deleted, 59 behavioral/contract tests added.

Production-readiness: `setup_assets.py` (announced, atomic, verified model
downloads), `python siri.py --doctor`, MCP binary discovery rewritten,
`preflight.py` false negative fixed, test-isolation `conftest.py` added
(`safety_engine`'s global cancel flag was leaking across tests and breaking MCP).

**MCP is proven end to end**: opens Notepad via UI Automation, discovers the
text control, types a unique marker, reads it back, confirms the text landed.
Stable 3/3. The allowlist is exactly right — 14/14 tools real, 0 phantom, and
the 4 gaps are precisely the 4 denied tools.

**Laya fallback** (`backend.py`): the Apache-2.0, Jev-compatible, non-generative
replacement for the paid TypeSafe API was already in `backend.py` since Phase 6
but unreachable — the fallback only pointed *toward* the paid API. Now
bidirectional: `FallbackBackend` + `get_shared_backend()` (process-wide).
The downgrade is never silent (`name` becomes `jev+laya`) and the confidence
gate follows the active engine.

## Critical hardware constraint — do not rediscover this

This machine is an **Intel i3-2350M** (Sandy Bridge, 2011, **no AVX2**).
Measured:

| Subsystem | Measured | Needs |
|---|---|---|
| Kokoro TTS | 33–58× slower than realtime | AVX2 |
| Laya decisions | **117.6 s median / 190 s max per turn** | AVX2 |

`HARDWARE_PROFILE.md` targets a dual-core i3-5005U, which is *also* below both
floors. The documented target hardware cannot run the local engines — this is
an unresolved contradiction worth fixing.

The Laya fallback therefore refuses rather than hanging. Refusal time went
63.7s → 27.1s → **0.0000s** via an AVX2 pre-check (`backend._cpu_has_avx2()`,
registry-based). Three bugs lived in that guard and all failed *open*:
an empty probe question set (laya short-circuits before inference, so the
guard always said "viable"), a `ctypes.__cpuid` AVX2 check that returned True
on a CPU without AVX2, and a second AVX2 check missing `import re`. All fixed
and pinned by tests. **When adding a guard here, test it against the real
backend, not a mock — two of those bugs looked correct because they returned
True.**

## Immediate next task (I was mid-edit; repo is clean but this is unfinished)

`tests/model_assets.py` was created and its import added to `test_phase1.py`,
`test_phase9c.py`, `test_phase10.py` — but **the decorators are not yet applied
to any test method.** So on a fresh CI runner these still FAIL rather than
xfail, and the badge goes red on first push:

```python
from model_assets import requires_kokoro, requires_silero_vad, requires_whisper_model
```

Apply them to:

- `test_phase9c.py::test_01_silero_vad_initialization_and_availability`
  — asserts `SileroVad().available`, needs `silero_vad.onnx`
- `test_phase10.py::test_08_jev_pronunciation_phonemization` — needs Kokoro
- `test_phase10.py::test_16_tts_status_reporting` — needs Kokoro
- `test_phase1.py::test_stt_backend_resolution_and_fallback` — needs Whisper model

Rule: xfail with an actionable reason, never silently skip. See
`tests/credentials.py` for the established pattern.

Then: add `python setup_assets.py --download` to the CI workflow so the common
path exercises the real models.

## Priority order from here

1. Finish the model-asset xfail gates above (CI red on push).
2. One-command MCP install — add the binary download to `setup_assets.py`
   using the **verified** source: release `v1.3.28`, asset
   `windows-mcp-server-1.3.28-win-x64.zip`, 54,186,365 bytes,
   SHA256 `9ca696…24fe4` (matches `SHA256SUMS.txt`). Upstream ships ZIPs, not
   a raw `.exe`.
3. Resolve the `HARDWARE_PROFILE.md` contradiction.
4. Push to GitHub and let CI run for real — it has **never executed**.

## Hard constraints that still apply

- **Never claim something works without measuring it.** Every number in the
  docs came from a run on this machine. If you state a fact, say where it came
  from.
- **Tests that cannot fail are worse than no tests.** Two of my own were
  caught in review this session: one used `inspect.getsource()` (the exact
  source-grep pattern Phase 31 deleted) and could not catch the real bug; one
  was a tautology. Both rewritten behaviourally.
- **Never skip silently.** Capability-dependent tests xfail with a reason.
- **Announce every suppression.** Silence is what reads as broken.
- The MCP binary is third-party (MIT) and gitignored — never commit it.