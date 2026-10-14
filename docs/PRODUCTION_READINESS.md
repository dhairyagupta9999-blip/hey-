# Production Readiness — What "anyone can clone and use it" actually requires

Status after Phase 31, the launch-critical pass, and the open-source research
pass. Ordered by what blocks a cloner, not by what is interesting to build.

## Verified working on a clean machine

| Area | Evidence |
|---|---|
| Clean install | All PyPI deps resolve; `winsdk` has cp312 win_amd64 wheels |
| Test suite | **841 passed, 0 failed**, 6 xfailed with actionable reasons |
| Preflight | **14 pass, 0 fail, 1 warn** (the warn is the absent API key) |
| Local routing | `open calculator` → ~6 ms, zero remote calls, real process launched |
| App index | 284 entries built from this machine in ~1.9 s |
| Memory | SQLite WAL verified |
| Safety | Run-dialog and `powershell.exe` strictly blocked |
| Secret scan | Zero API keys in disk logs |
| Speech out | Kokoro int8 verified synthesising real audio (24 kHz, peak 0.70) |
| Speech in | faster-whisper warm and functional (5394 ms) |
| MCP | Handshake succeeded; Notepad opened, typed into, and read back |
| Git reconciliation | Hermetic test passes with git, xfails cleanly without |

The xfails are honest and self-describing: 4 need an external OpenDots
checkout, 2 need a provider credential. None is a silent skip.

## Hardware floors — measured, not assumed

This machine is an **Intel i3-2350M** (Sandy Bridge, 2011, no AVX2). Two
subsystems are unusable on it, and both now say so instead of hanging:

| Subsystem | Measured | Required |
|---|---|---|
| Kokoro TTS | 33–58× slower than realtime (a 0.8 s reply takes 47 s) | AVX2 |
| Laya decisions | 36–190 s per turn (median 117.6 s) | AVX2 |

Both are correct software; the CPU is the constraint. The fallback now
pre-checks AVX2 and refuses in **0.0000 s** rather than freezing — see
`docs/OPEN_SOURCE_RESEARCH.md` §1.1.

**Note that `HARDWARE_PROFILE.md` targets a dual-core i3-5005U, which is also
below both floors.** The documented target hardware cannot run the local
engines. This belongs in the README (it is now) and in any honest capability
table.

## Still open

1. **CI has never run.** `.github/workflows/windows.yml` is written, but this
   machine had no git remote. The badge is unknown until pushed.
2. **The conversational half still needs `TYPESAFE_API_KEY`** on this hardware,
   because the local fallback is (correctly) refused here. On AVX2 machines the
   fallback works; on this one it cannot.
3. **Laya zero-shot accuracy** is poor (~3 of 8 probes). Upstream reports
   0.362 base vs 0.766 fine-tuned; fine-tuning on this project's command
   vocabulary is the next real step.
4. **README install path unverified on a genuinely clean machine** — this one
   started with an empty Python and no git.

## Blocking a public launch

1. ~~**No working TTS on a fresh machine.**~~ **FIXED** — `setup_assets.py`
   fetches the Kokoro int8 model (108.8 MB), voice table (26.9 MB) and Silero
   VAD (2.2 MB) from their official releases. Downloads are announced with size
   and destination before any byte moves, written to a `.part` file and renamed
   atomically, and verified against `Content-Length`. Run
   `python setup_assets.py --download`.
2. **The conversational half needs `TYPESAFE_API_KEY`.** `open calculator` works
   offline; *"click the first video"*, *"put this prompt in code"* all route to
   `api.typesafe.ai`. Without a key they fail outright.
3. **CI has never run.** `.github/workflows/windows.yml` is written but this
   machine has no git remote. The badge will be unknown until pushed.
4. **`preflight.py` is opt-in.** It should gate first run and release. It is
   the best asset in the repo and it currently sits unused.

## TTS performance — measured, and hardware-dependent

Verified working end to end on this machine: Kokoro loaded and synthesised
1.32 s of real audio at 24 kHz, peak amplitude 0.70 (audible).

It is also **33–58× slower than realtime** here:

| Measurement | Value |
|---|---|
| Cold engine load | 6.1 s (once) |
| Warm synthesis, 1.32 s of audio | 43.2 s (32.7× realtime) |
| Warm synthesis, 0.81 s of audio | 47.2 s (58.3× realtime) |

**Cause: this CPU has no AVX2.** It is an Intel Core i3-2350M (Sandy Bridge,
2011, Family 6 Model 42). AVX2 arrived with Haswell in 2013. Without it, ONNX
Runtime's int8 quantized operators are *emulated* rather than
hardware-accelerated, which is what the 30–50× penalty is.

This is a hardware floor, not a defect in the code — `tts.py` configures
`intra_op_num_threads = 2` correctly for a dual-core.

Practical guidance:

| Hardware | Local Kokoro |
|---|---|
| Haswell (2013) or newer, AVX2 | Usable |
| Pre-Haswell (this machine) | **Not usable** — use Fish Audio, or accept 30–50 s replies |

The project's own `HARDWARE_PROFILE.md` targets a dual-core i3-5005U, so the
documented target hardware is itself below the local-TTS floor. Anyone on a
pre-2013 laptop needs the cloud voice. This should be stated in the README
rather than discovered after a 47-second wait.

## Remaining test failures (all environment, none code)

| Test | Cause | Resolves with |
|---|---|---|
| `test_phase29::test_22`, `test_phase6` ×2 | No `PySide6` | `pip install -r requirements.txt` |
| `test_phase1` | No `faster_whisper` | same |

Every one of these is an uninstalled optional dependency, not a defect.

## Test isolation — fixed

`test_phase6.py` calls `safety_engine.request_global_cancel()` and never clears
it. `_CANCEL_EVENT` is process-global, so every *later* test saw
`is_cancel_requested() == True`, and `mcp_client.call_tool()` refused
everything as "cancelled". The Windows-MCP tests failed in the full suite with
`Action cancelled.` while passing perfectly in isolation.

A test failing only in the full suite, for a reason three files away, is the
worst kind of failure to debug. `tests/conftest.py` now clears the cancel flag,
any pending confirmation, and `HEYJEV_*` environment variables around every
test. A root `conftest.py` also makes the repo importable regardless of CWD.

`mcp_client.start()` additionally stopped reporting a generic *"Windows
automation service is unavailable"* and now names the real cause — which is
what finally exposed the cancel-flag leak.

## Independent review

An independent review of this session's changes returned 6 HIGH findings. All
are fixed; the two that mattered most are described in
`docs/OPEN_SOURCE_RESEARCH.md` §1.1 — a latency guard that was dead code, and
two AVX2 checks that failed **open**. It also caught:

- `siri.py` built a fresh composite backend **per utterance**, discarding the
  probe verdict and reloading model weights every turn. Now process-wide.
- The §4.6 confirmation fix was silently overridden at all four MCP call sites,
  which passed `timeout_s=8.0` explicitly. `timeout_s` was also never *stored*
  on the pending confirmation, so `respond()` always re-read a default.
- Two of my own tests were defective: one used `inspect.getsource()` — the exact
  source-grep pattern Phase 31 deleted — and could not catch the real bug; one
  was a tautology that could not fail. Both rewritten behaviourally.

## MCP — status

The client is already built (`mcp_client.py`, wired at `tier3_agent.py:39`) with
an allowlist, per-tool risk levels, safety-engine gating, and `<MCP_DATA>`
prompt-injection quarantine. PowerShell, registry and file-write are denied by
default.

Done in this pass:
- Binary discovery rewritten — `mcp/Sbroenne.WindowsMcp.exe` at the repo root is
  the documented drop location; env var and `%LOCALAPPDATA%` also searched.
  Previously the only path was one developer's `.venv-agents/`.
- `mcp/` scaffolded with a README explaining why the binary is not vendored.
- README section documenting install and the risk model.
- 5 behavioural tests: allowlist coverage, deny-by-default, risk gating, and
  real-server tests that xfail with an actionable message until the binary is
  present.

Outstanding: the binary itself (third-party, MIT, not redistributable here),
and the `browser-use` + filesystem MCP servers from option 3.

## Deliberately not done

- Vendoring the MCP binary — third-party, ~57 MB, own licence.
- Shipping a default API key — nobody should rely on someone else's.