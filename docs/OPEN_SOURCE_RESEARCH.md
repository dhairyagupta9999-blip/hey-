# Open-Source Landscape — What Actually Replaces TypeSafe, and What To Connect

Research date: 2026-10-14. Every claim here is either a primary-source quote or
a measurement taken on this machine. Where something is unverified, it says so.

---

## 1. The TypeSafe question — answered, and it was already half-built

You asked for an open-source alternative to the hosted decision API. One
exists, it is Apache-2.0, it is explicitly Jev-compatible, and **this repo
already contains a client for it that nobody uses.**

### Laya — open-source, local, Jev-compatible

| Property | Value |
|---|---|
| Upstream | ConvAI Innovations · `convaiinnovations/laya` (HuggingFace) |
| Licence | **Apache-2.0** (weights included) |
| Runtime | `pip install laya` · ONNX Runtime option · Node package `@receptron/laya` |
| API shape | *"the same as the Python reference implementation and as TypeSafe Jev's `system_one` API"* |
| Latency | ~33 ms — **on GPU / Apple silicon, not CPU** |
| Languages | 100+ via the multilingual checkpoint |
| Failure mode | Never generates text → nothing to parse, **nothing to hallucinate** |

From the upstream README, verbatim:

> Run Laya — the open-source, Jev-compatible System 1 decision model by Convai
> Innovations — from Node.js / TypeScript. […] The request/response shape is the
> same as the Python reference implementation (`RLAgent.system_one`) and as
> TypeSafe Jev's `system_one` API, and the output matches the Python
> implementation to four decimal places.

And from the model card:

> Laya does not generate text, so there is nothing to parse and nothing to
> hallucinate. Trained with reinforcement learning against strictly proper
> scoring rules (RLCD), so reporting honest probabilities is the only way to
> maximise reward.

**Why this matters more than it looks.** `PROJECT_VISION.md` says *"Never report
that an action succeeded unless verified."* A non-generative classifier that
emits calibrated probabilities is structurally aligned with that principle — it
cannot invent an action. A generative LLM in the decision path can.

### It is already in this repo, unused

`backend.py` contains `LayaBackend` — *"Local open-weight Laya decision backend
running non-autoregressively on CPU"* — with `fallback_to_jev=True` and a
different `default_gate` (0.45 vs 0.65). `tier3_agent.py` and
`benchmark_backends.py` reference it. `requirements.txt` already pins
`laya>=0.3.24` (PyPI has 0.4.7).

It is not the default (`HEYJEV_BACKEND` defaults to `jev`), and it is not
documented. **The fastest path to "no paid API key" is to finish an integration
that is 80% complete, not to adopt something new.**

### What dropping TypeSafe actually buys

- No `TYPESAFE_API_KEY`. The top launch blocker disappears.
- No per-turn cost. The README's `~$0.00004/turn` claim becomes `$0.00000`.
- No network hop for the decision path — this is the *other* half of the 1,200 ms
  B1 was chasing, and it applies even when routing is correct.
- Apache-2.0 is compatible with this project's MIT redistribution.

### Honest caveats

- **Download size.** The English checkpoint is ~808 MB; the Node ONNX bundle is
  ~1.7 GB fp32 and budgets ~2 GB RAM. Selective subfolder download means you only
  fetch the checkpoint you ask for. This is a big first-run download and must be
  announced, not silent — same principle as `setup_assets.py`.
- **Hardware.** 421M params (English) / 322M (multilingual). On this machine —
  an i3-2350M, pre-Haswell, no AVX2 — expect far worse than the advertised
  33 ms. **Measurement in progress; see §1.1.**
- **Fine-tuning matters.** Upstream reports 0.766 accuracy for the
  `laya-typed-decisions` checkpoint vs 0.362 for base English on their
  typed-decisions benchmark. The base checkpoints "work zero-shot", but for this
  project's specific command vocabulary, fine-tuning is where the accuracy jump
  is. There is a free Kaggle 2×T4 notebook for the whole loop.
- **Drop-in endpoint exists.** Laya ships `laya[serve]` exposing `/v1/systemone`,
  so it can stand in behind the existing `JevBackend` HTTP call with no change to
  `siri.py`. That is the lowest-risk integration path by far.

### 1.1 Measured on this machine

See **`docs/LAYA_MEASUREMENT.md`** for the full run. Headline, honestly:

| | |
|---|---|
| Cold load | 0.5 s |
| Warm median | **117.6 s** per turn |
| Warm max | **189.8 s** per turn |
| Accuracy | ~3 of 8 probe utterances clearly correct |

Advertised is ~33 ms. On this pre-Haswell CPU it is ~3,500× slower, and
zero-shot accuracy is poor — which upstream itself predicts (0.362 base vs 0.766
fine-tuned).

**So Laya is the right architecture and the wrong default for this hardware.**
The fallback is kept, but guarded in two layers:

1. **AVX2 pre-check (instant).** `backend._cpu_has_avx2()` reads the CPU
   identifier from the registry. Intel Family 6 below model 60 (anything
   pre-Haswell) is rejected without running anything.
2. **Timed probe (fallback for unknown hardware).** If the CPU passes the
   pre-check, one real turn is timed against `HEYJEV_FALLBACK_BUDGET_S`
   (default 5 s). Exceeding it refuses to fall back.

Measured on this machine:

| Stage | Refusal time |
|---|---|
| No guard (would silently "work", then freeze) | 63.7 s per turn |
| Timed probe only | 27.1 s to detect, then refuse |
| **AVX2 pre-check + probe** | **0.0000 s** |

Two bugs were found and fixed in this guard itself, both of the same shape —
a check that **looked** like it worked:

- The first probe passed an **empty** question dict. `laya` short-circuits
  before inference on empty questions, so the probe returned in ~0 ms and the
  guard always reported "viable". The guard was dead code that looked alive
  because it returned `True`.
- The first AVX2 check used `ctypes.__cpuid` through `windll`, which reported
  **True on an i3-2350M** — a CPU that definitively lacks AVX2. A second
  version used `re` without importing it, raised `NameError`, and the bare
  `except` returned `True` again. Two silent wrong answers in a row, both
  failing *open*. It now reads the registry and is pinned by a test that
  asserts `False` on pre-Haswell silicon.

Every one of those would have shipped as "the guard is in place."

---

## 2. MCP — what to connect

### Primary (already selected, already wired)

`sbroenne/mcp-windows` — MIT, standalone .NET binary, **zero telemetry
independently verified via psutil in the original Phase 7 research**, and
semantic UI Automation rather than blind coordinates. Tools: `ui_snapshot`,
`ui_find`, `ui_click`, `ui_type`, `ui_select`, `ui_read`, `ui_wait`,
`keyboard_control`, `window_management`, `app`, `clipboard`, `file_open`,
`file_save`, `ui_read_table`, `ui_batch`, `process`.

This is the right pick for the "open Notepad, understand what's happening"
requirement, because UI Automation resolves elements by name/role rather than
guessing coordinates — which is also why DPI scaling and window position stop
being a reliability problem.

### Alternatives worth knowing

| Repo | Note |
|---|---|
| `locomorange/uiautomation-mcp` | Python Windows UIA MCP server. Lighter install (no .NET), fewer tools. |
| `mario-andres-s/mcp-windows-desktop-automation` | Positioned as a "trusted-owner" server for an interactive desktop. |
| `CursorTouch/Windows-MCP` | The repo's own Phase 7 fallback. Has `powershell_tool` / `registry_tool` — **a worse fit here**, since this project's safety engine already denies those by default. |

### Official registry

`registry.modelcontextprotocol.io/v0.1/servers` — the canonical index. Worth
preferring over the "best MCP servers 2026" listicles, which are SEO content with
no reliability signal.

### Method note

Most search results for "best MCP servers 2026" were listicles with star-count
rankings and no verification. I went to primary sources (upstream READMEs, model
cards, the official registry) for everything claimed above.

---

## 3. Worth adopting, ranked by value per unit of risk

| # | Project | Value | Risk |
|---|---|---|---|
| 1 | **Laya as default backend** | Removes the paid key entirely | Med — needs benchmark on real hardware |
| 2 | **`setup_assets.py` for Laya weights** | Honest first-run download | Low |
| 3 | **`laya[serve]` behind the existing endpoint** | Lowest-risk integration; no change to `siri.py` | Low |
| 4 | **mcp-windows binary + Notepad proof** | The UI-automation scenario | Low |
| 5 | **`laya[mcp]`** | Laya already ships an MCP server — could expose decision tracing to external agents, matching this project's existing OpenDots gateway thesis | Low |
| 6 | **Self-hosted voice pipeline patterns** (bigiron.cc guide) | Confirms the current stage choices: openWakeWord → Silero VAD → faster-whisper → local intent → Kokoro. Hey Jev is already on this stack. | None |

### Deliberately not recommended

- **A generative local LLM (Ollama / Qwen / Llama) in the decision path.** It
  would be a large download, slow on this hardware, and it reintroduces exactly
  the hallucination surface that Laya is designed to eliminate. The project's own
  vision doc argues against it.
- **Agent-S, UFO3, browser-use.** Large (2.5 GB / 1.2 GB / 350 MB), and two of
  the three send screenshots to external APIs. Not a fit for a privacy-positioned
  local assistant.

---

## 4. What was actually done with this research

### The fallback was one-directional — now fixed

`backend.py` gained `FallbackBackend` and `get_backend_with_fallback()`. The
composed backend tries the configured primary, and on **any** failure (no key,
401, dead network) continues locally on Laya.

Three properties matter, and each has a test:

- **The downgrade is never silent.** `name` becomes `jev+laya`, so every trace,
  log line and cost entry shows which engine actually answered.
- **The confidence gate follows the engine that answered.** Laya's calibrated
  probabilities use 0.45, Jev's 0.65. Using the wrong one either over- or
  under-fires. `default_gate` is read from the active backend.
- **Cost is reported truthfully.** A local turn costs `$0.00000`; the README's
  `~$0.00004/turn` becomes $0 for keyless users.

`siri.py` now calls `get_backend_with_fallback()` on the decision path, and the
401 handler says both paths failed rather than blaming a key that may be
irrelevant.

Disable with `HEYJEV_LOCAL_FALLBACK=0`.

### A 2.5 GB bug found while measuring

`LayaBackend._ensure_loaded()` called `Router(device="cpu", default=..., preload=True)`.
Upstream's `preload=True` loads **all three checkpoints up front** — roughly
2.5 GB of combined weights — when this backend only ever asks questions of one.
A measured run performed three separate download sequences (~4 minutes each)
before answering a single question.

Now `preload=False`: only the named checkpoint is fetched. This is the same
class of defect as the silent 250 MB download the project already complains
about, one order of magnitude larger.

### Calibration caveat

Upstream emits at load time:

> this checkpoint ships invalid temperatures or values outside [0.5, 5]; …
> Treat confidence from the affected entries as uncalibrated.

Observed on the multilingual checkpoint (`choice:11`). Entry 11 of this
project's battery — `app_action` has six criteria, so this is a later
question — falls back to an uncalibrated 0.5. Confidence for those entries
should not be trusted for gating until upstream recalibrates.

---

## 5. The honest bottom line

The project did not have a technology problem. It had a **wiring problem**: the
Apache-2.0 local replacement for its one paid dependency has been sitting in
`backend.py` since Phase 6, fully implemented, unreachable, with a fallback that
only pointed the wrong way.

Fixing that is smaller than any feature on the backlog, and it removes the top
launch blocker.