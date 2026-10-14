# Hey Jev: Current Repository State & Handoff Baseline

This document captures the exact, verified technical baseline of Hey Jev as of October 12, 2026.

---

## 1. Git State & Remote Synchronization

- **Repository Path:** `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev`
- **Active Branch:** `windows-port`
- **Pre-Handoff Base Commit:** `f74aa08` (`docs: manual acceptance testing guide for Hey Jev`)
- **Remote URL:** `https://github.com/dhairyagupta9999-blip/hey-.git`
- **Remote Synchronization:** Local branch `windows-port` is **ahead of origin/windows-port**.
- **Remote Visibility:** Private repository.
- **Working Tree Status:** Clean (no unstaged modifications, no untracked files).

---

## 2. Verified Test Results on this Checkout

### 2.1 Complete Automated Regression Suite
- **Command:** `.venv\Scripts\pytest.exe -q`
- **Total Tests:** 832
- **Passed:** 832
- **Failed:** 0
- **Skipped:** 0
- **Duration:** 346.33 seconds (5 minutes 46 seconds)
- **Status:** **100% Passing**

### 2.2 Preflight Truth Verification Suite
- **Command:** `.venv\Scripts\python.exe preflight.py`
- **Results:** **13 pass, 0 fail, 1 warn**
- **Warning Details:**
  `[WARN] MCP server binary exists but failed handshake: Windows automation service is unavailable. Please check MCP server installation.`
  *(Expected behavior: The optional standalone external Windows MCP automation daemon is not currently active. System falls back automatically to native UIA and Win32 automation.)*

---

## 3. Verified Startup Entrypoints

1. **Desktop GUI Application (Primary):**
   ```powershell
   .venv\Scripts\python.exe app.py
   ```
2. **Headless Voice Daemon (CLI):**
   ```powershell
   .venv\Scripts\python.exe siri.py
   ```
3. **OpenDots MCP Gateway (Loopback HTTP):**
   ```powershell
   .venv\Scripts\python.exe opendots_gateway.py --host 127.0.0.1 --port 8765
   ```
4. **Preflight Hardware & Truth Check:**
   ```powershell
   .venv\Scripts\python.exe preflight.py
   ```

---

## 4. Known User-Facing Issues & Diagnostic State

| Issue | User Symptom | Current Diagnostic Assessment |
| :--- | :--- | :--- |
| **Calculator Launch Latency** | Multi-second delay before Calculator appears. | Omission of `calculator` from `apps.json` routes command through 1,200ms remote TypeSafe battery before Tier 2 fallback; `cmd /c start calc.exe` adds process spawn overhead. |
| **YouTube Opening Failure** | YouTube does not open or browser fails. | Compound phrasing (`"open youtube in chrome"`) misses `KNOWN_SITES` regex; Tier 3 browser agent runs `--headless=new` by default. |
| **STT Unreliability / Drops** | Commands misheard or ignored on quick repetition. | Guard b silently drops identical commands repeated within 5.0 seconds — confirmed and real (B3). Acoustic confusion was previously attributed to a `tiny.en` model, but the deployed default is `small.en` (B5), so the acoustic cause is unverified; measure the running model before drawing conclusions. |

Detailed root cause analysis and diagnostic entrypoints are documented in **[KNOWN_ISSUES.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/docs/claude-handoff/KNOWN_ISSUES.md)**.

---

## 5. Recommended Next Actions for Claude Code

When beginning work on this codebase:
1. **Do NOT begin a new feature phase (Phase 30).**
2. **First Priority:** Resolve the latency and routing issues for Calculator:
   - Add `calculator` and `calc` to `apps.json`.
   - Ensure `is_tier2_direct_match` catches all common calculator launch phrasings without remote LLM roundtrips.
   - Profile `actions_win.py:launch_app` to benchmark direct execution vs `cmd /c start`.
3. **Second Priority:** Harden browser website navigation:
   - Update `target_extractor.py` and `system_targets.py` to extract target URLs even when phrased with browser suffixes (`"in Chrome"`, `"on Edge"`).
   - Ensure `BrowserAgent` uses `headless=False` when user explicitly requests opening a visible website.
4. **Third Priority:** Tune voice recognition deduplication:
   - Adjust Guard b deduplication window in `siri.py` from 5.0s to a more responsive duration (e.g. 2.0s) or log when dropped so the user is informed.
5. **Validation Gate:** Run `.venv\Scripts\pytest.exe -q` and `.venv\Scripts\python.exe preflight.py` to ensure all 832 regression tests remain green after each change.
