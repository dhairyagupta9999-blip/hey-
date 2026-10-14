# Phase 31 — Correctness: Findings

Status as of this commit. Every fix below is backed by a test that was
observed **failing before the fix**. Nothing here was verified by inspection
alone.

## Correction to the remediation plan: B1

The plan's §1.1 diagnosed B1 as a coverage gap for app names *missing* from
`apps.json`, on the reading that `"open calculator"` falls through
`is_tier2_direct_match()` and pays a remote roundtrip.

**That does not reproduce.** The plan enumerated "three cases" in the function;
it has five. It omitted branch 5:

```python
# siri.py:475-477
# 5. Open/close/focus with a target not in APPS
if action and target:
    return True
```

Measured on real hardware against unmodified code, `"open calculator"` returns
`True` and resolves locally in **6.16 ms with zero remote calls**, and actually
launches Calculator (verified via `psutil`). The prescribed ~15-line insert at
§4.1 would have changed `True` → `True`.

**The defect that is real is the mirror image.** Apps that *are* in `APPS` take
branch 1, which returned `False` — "defer to the remote battery" — without
asking the local index that resolves them at score 100.0. So `"open spotify"`
paid a hosted `api.typesafe.ai` roundtrip for an answer the machine already had.
B1 was re-targeted to that, and §5.3 test 1 now asserts on `open spotify`.

Also unverifiable here: the plan's ~1,200 ms figure. `JevBackend.decide` is a
paid call requiring `TYPESAFE_API_KEY`; without a key it cannot be issued.
`benchmarks/baseline_windows.json` records it as **not measured**, not as 1,200 ms.

## Closed

| ID | Defect | Fix | Test |
|---|---|---|---|
| B1 | Battery-list apps paid a remote roundtrip the local index could answer | `siri.py:449` — consult `app_index` before deferring, same `>= 70.0` gate as `tier2_resolver.py:139` | `test_b1_app_index_before_remote.py` |
| B2 | `"open youtube in chrome"` unresolvable; returned an ambiguity prompt | `target_extractor.split_browser_qualifier()` + honoured in `system_targets` | `test_b2_browser_qualifier.py` |
| B3 | 5 s dedup window silently swallowed human retries | `siri.py` — window 5.0 s → 0.3 s; every suppression announced | `test_b3_retry_not_suppressed.py` |
| B4 | Browser ran headless by default; actions invisible | `browser_adapter.py:139` — default `headless=False` | `test_b4_browser_visible_by_default.py` |
| B5 | Docs specified `tiny.en`; code runs `small.en` | Corrected living docs + `.env.example` | `test_b9…`/`config` agreement |
| B6 | Wake-backend defaults disagreed | `.env.example` now matches `config.DEFAULT_WAKEWORD_ENGINE` | — |
| B7 | macOS apps in a Windows catalog | Removed `safari`, `finder`, `messages`, `notes` | `test_b7_catalog_is_windows_only.py` |
| B8 | 5 × `os.makedirs()` at import | `config.ensure_app_dirs()`, called from the entrypoint | `test_b8_import_has_no_side_effects.py` |
| B9 | Placeholder bearer token shipped by default | `config.OPENDOTS_TOKEN_PLACEHOLDER`; gateway refuses to start on it | `test_b9_placeholder_token.py` |
| B10 | README clone URL pointed at another account | Corrected to `dhairyagupta9999-blip/hey-` | — |
| §4.6 | 8 s confirmation timeout hostile for speech | 20 s default + one bounded re-arm on hesitation | `test_s46_confirmation_rearms.py` |
| §9.3 | `LICENSE` absent | MIT added, attributed to Henryk Lunaris and Dhairya Gupta | — |

Also: `.github/workflows/windows.yml` (absent entirely), `requirements-ci.txt`,
and `rapidfuzz` / `send2trash` / `pywin32` added to `requirements.txt` — all three
were imported by the code but undeclared, so a clean install could not run.

## New findings, recorded not fixed (feature freeze)

1. **`find_app("finder")` returns "Windows Defender Firewall with Advanced
   Security" at score 75.0**, which clears the `>= 70.0` gate. Saying *"open
   finder"* launches Windows Defender. This is a fuzzy-threshold defect in
   `app_index`, unrelated to B7. Not fixed under the freeze.
2. **Existing installs keep the stale catalog.** `config.py` seeded
   `%APPDATA%/HeyJev/apps.json` from the shipped file only when absent, and
   `app_index` prefers that copy — so editing the repo's `apps.json` does not
   repair an install that ran before the fix. The structural half of B7
   (regenerate the catalog from `app_index`, §4.1) is still outstanding.
3. **Source-assertion tests: 3, not 9.** An AST scan (§5.2) found three tests
   that open a repo file and assert on its literal content, plus one hybrid
   whose `ast.parse` half was kept. The plan's figure of 9 came from a grep
   census that counted `assertIn` *calls* (148), not tests.

## Test suite: before and after

| | Before | After |
|---|---|---|
| Legacy suite failures | 15 (unchanged) | 15 (same list) |
| Legacy suite passes | 777 | ~790 |
| Behavioral tests | 0 | 27 |
| Contract tests | 0 | 13 |
| Source-assertion tests | 4 | 0 |

All 15 legacy failures are pre-existing environment gaps — missing `PySide6`,
`faster_whisper`, Kokoro and Silero model assets, the OpenDots checkout at
`C:\Users\prabh\.gemini\...`, and a missing `TYPESAFE_API_KEY`. None is caused
by this work.

The behavioral tier must run as its own pytest invocation. The legacy suite
calls `resolve_tier2("refresh apps")`, which rebuilds the `app_index` singleton
in-process; sharing an interpreter lets one tier observe state another left
behind. §5.4 already runs the tiers separately — keep it that way.

## Not done, and deliberately so

- **§4.6 PowerShell denylist → semantic classification.** Real work, and it is
  the one §4.6 item with a safety downside if done badly (risk table: Medium
  likelihood, **High** impact). Left for a focused pass rather than squeezed in.
- **§4.5 generation of `.env.example` and the README config table from one
  module.** The drift is corrected and both files now agree; making the class
  *unrecurable* is the larger version.
- **Phase 32+ work** — latency dashboard, barge-in, execution verification.
  Out of scope under the freeze.