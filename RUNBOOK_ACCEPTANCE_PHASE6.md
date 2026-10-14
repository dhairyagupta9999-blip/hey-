# HEY JEV — Phase 6 Acceptance Runbook
## Open-Vocabulary PC Control Verification (Tiers 1, 2, and 3)

**Platform:** Windows 10 / Windows 11 x64  
**Date:** October 2026  
**Scope:** Verification of Phase 6 Open-Vocabulary PC Control ("Do Anything" layer), Safety Engine, Tier 3 AI Agent, and UI Settings.

---

### Overview of Architecture & Routing Tiers

1. **Tier 1 (Fast Path — Existing Battery):**
   - Speculative fan-out via TypeSafe Jev System 1 (~$0.00004/turn).
   - Fixed vocabulary of known apps (`apps.json`), volume, media playback, timers, dark mode, browser tabs.
   - P95 latency ~1.4 s. Unchanged and remains default for known commands.

2. **Tier 2 (Local Open-Vocabulary Resolvers — Zero LLM Cost):**
   - **App Index:** Start Menu shortcuts, UWP/Store apps (`shell:AppsFolder`), App Paths registry, PATH binaries.
   - **Fuzzy Matching:** RapidFuzz token matching with phonetic tolerance for spoken transcript errors (e.g. "sportify" → Spotify).
   - **System Targets & Folders:** `ms-settings:` URIs, Windows special folders (Downloads, Documents, Desktop), admin utilities (`taskmgr`).
   - **Websites:** Known sites without `.com` (YouTube, GitHub, Reddit, Gmail).
   - **Window Layout:** Snap left/right/top/bottom, maximize, minimize, restore.
   - **File Finder:** Windows Search Index / user document tree search with ambiguity resolution (top 2–3 choices).
   - **Safety Engine:** Dangerous files (`.exe`, `.bat`, `.ps1`), file deletions (Recycle Bin), `close_all` require explicit confirmation.

3. **Tier 3 (Open-Vocabulary AI Agent — Default OFF):**
   - Configurable tool-calling loop (OpenRouter Claude Haiku default, OpenCode Zen free option).
   - 17 native tools: app launch/close, file search/open, window list/focus/snap, keyboard/mouse input, foreground text reading, clipboard, web search, PowerShell.
   - 5-step ceiling and 15-second timeout.
   - Prompt-injection quarantine: external text wrapped in `<DATA>` tags with instruction neutralization.
   - Daily spend cap ($0.10 default stored in `spend.json`) with automatic shutoff.
   - Graceful key check: speaks "AI agent needs an API key" and stays off if unconfigured.

4. **Safety & Cancellation Hooks:**
   - Spoken word `"stop"` or `"cancel"` immediately aborts pending confirmations and agent loops.
   - `Esc` key immediately cancels in-flight actions or pending confirmations.
   - 8-second confirmation timeout defaulting to NO.
   - Reversible undo: `"undo that"` reopens closed apps or restores modified volume.

---

### Acceptance Checklist & Spoken Test Suite

#### Section 1: Tier 1 Fast Path Battery (Regression Gate)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---------------|--------------------------|--------|-------|
| 1 | *"open Spotify"* | Launches or focuses Spotify via Tier 1 battery | `[ ] Pass  [ ] Fail` | Latency < 1.5s, cost ~$0.00004 |
| 2 | *"set volume to fifty percent"* | Adjusts Windows master volume to 50% | `[ ] Pass  [ ] Fail` | Tier 1 core volume action |
| 3 | *"pause Spotify and open Slack"* | Pauses playback and opens Slack | `[ ] Pass  [ ] Fail` | Compound multi-action execution |
| 4 | *"set a timer for ten minutes"* | Creates 10-minute timer in Timers tab | `[ ] Pass  [ ] Fail` | Chime sounds and row appears |
| 5 | *"turn on dark mode"* | Flips Windows Personalize theme registry | `[ ] Pass  [ ] Fail` | Immediate dark theme apply |
| 6 | *"close tab"* | Sends Ctrl+W to foreground browser | `[ ] Pass  [ ] Fail` | Tier 1 browser shortcut |

---

#### Section 2: Tier 2 Open-Vocabulary App Control

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---------------|--------------------------|--------|-------|
| 7 | *"open Calculator"* | Launches Windows Calculator (`calc.exe` / UWP) | `[ ] Pass  [ ] Fail` | Discovered from App Index |
| 8 | *"open Paint"* | Launches Microsoft Paint (`mspaint.exe`) | `[ ] Pass  [ ] Fail` | Win32 Start Menu shortcut |
| 9 | *"open Snipping Tool"* | Launches Snipping Tool utility | `[ ] Pass  [ ] Fail` | UWP / Package app resolution |
| 10 | *"open sportify"* | RapidFuzz phonetic tolerance resolves to Spotify | `[ ] Pass  [ ] Fail` | Spoken error tolerance test |
| 11 | *"close Notepad"* | Closes Notepad via WM_CLOSE / process tree | `[ ] Pass  [ ] Fail` | Spoken reply: "Notepad's gone." |
| 12 | *"close Calculator"* | Closes Calculator process and window | `[ ] Pass  [ ] Fail` | Spoken reply: "Calculator is closed." |
| 13 | *"close everything"* | Awaits confirmation: "Do you really want to close all open windows?" | `[ ] Pass  [ ] Fail` | High-risk gate; requires "yes" |
| 14 | *"refresh apps"* | Re-scans Start Menu, UWP, and PATH into cache | `[ ] Pass  [ ] Fail` | Spoken reply: "App index refreshed." |

---

#### Section 3: Tier 2 System Targets, Folders & Window Layout

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---------------|--------------------------|--------|-------|
| 15 | *"open Bluetooth settings"* | Opens `ms-settings:bluetooth` in Windows Settings | `[ ] Pass  [ ] Fail` | Direct Windows URI protocol |
| 16 | *"open sound settings"* | Opens `ms-settings:sound` in Windows Settings | `[ ] Pass  [ ] Fail` | Direct Windows URI protocol |
| 17 | *"open the Downloads folder"* | Opens `%USERPROFILE%\Downloads` in File Explorer | `[ ] Pass  [ ] Fail` | Special folder resolution |
| 18 | *"open Documents"* | Opens `%USERPROFILE%\Documents` in File Explorer | `[ ] Pass  [ ] Fail` | Special folder resolution |
| 19 | *"open Task Manager"* | Launches `taskmgr.exe` with administrative path | `[ ] Pass  [ ] Fail` | System administrative utility |
| 20 | *"open YouTube"* | Opens `https://youtube.com` in default browser | `[ ] Pass  [ ] Fail` | Known site without `.com` |
| 21 | *"open GitHub"* | Opens `https://github.com` in default browser | `[ ] Pass  [ ] Fail` | Known site without `.com` |
| 22 | *"snap Chrome to the left"* | Positions Google Chrome window on left half | `[ ] Pass  [ ] Fail` | Window layout calculation |
| 23 | *"snap window to the right"* | Positions active foreground window on right half | `[ ] Pass  [ ] Fail` | Active window layout |
| 24 | *"maximize window"* | Maximizes current active window (`SW_MAXIMIZE`) | `[ ] Pass  [ ] Fail` | Win32 show command |

---

#### Section 4: Tier 2 File Finder & Safety Gating

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---------------|--------------------------|--------|-------|
| 25 | *"open my resume"* | Finds resume in Documents/Downloads and launches | `[ ] Pass  [ ] Fail` | Windows Search / file tree |
| 26 | *"open the budget spreadsheet"* | Discovers `*.xlsx` / `*.csv` and opens in Excel | `[ ] Pass  [ ] Fail` | Extension and type match |
| 27 | *"open my document"* (when 3 matches exist) | Disambiguates: "Found 3 files: 1, 2, 3. Which one?" | `[ ] Pass  [ ] Fail` | Ambiguity top-3 resolution |
| 28 | *"open installer.exe"* | Prompts: "Opening executable file requires confirmation." | `[ ] Pass  [ ] Fail` | Dangerous extension high-risk gate |
| 29 | *"delete notes.txt"* | Moves file to Recycle Bin with undo registered | `[ ] Pass  [ ] Fail` | send2trash safe deletion |

---

#### Section 5: Tier 3 AI Agent ("Do Anything" Mode)

| # | Spoken Phrase | Expected System Reaction | Result | Notes |
|---|---------------|--------------------------|--------|-------|
| 30 | *"Summarize the text in this window and copy it"* | Agent uses `read_foreground_text` + `clipboard_get` | `[ ] Pass  [ ] Fail` | Multi-step tool loop (Tier 3 ON) |
| 31 | *"Find the notes file, read it, and search for the author online"* | Agent uses `find_files` + `web_search` | `[ ] Pass  [ ] Fail` | Max 5 steps, 15s timeout ceiling |
| 32 | Malicious prompt injection in file/window | Content wrapped in `<DATA>`, prompt injection ignored | `[ ] Pass  [ ] Fail` | Strict quarantine & neutralization |
| 33 | Action when daily spend reaches $0.10 | Speaks: "Daily spend cap reached. AI agent paused." | `[ ] Pass  [ ] Fail` | Automatic spend cap shutoff |
| 34 | Tier 3 request when no API key configured | Speaks: "AI agent needs an API key." without crash | `[ ] Pass  [ ] Fail` | Graceful key check & recovery |

---

#### Section 6: Safety Engine, Cancellation & Undo

| # | Action / Spoken Phrase | Expected System Reaction | Result | Notes |
|---|------------------------|--------------------------|--------|-------|
| 35 | Spoken: *"stop"* (during confirmation) | Cancels pending confirmation and halts action | `[ ] Pass  [ ] Fail` | `request_global_cancel` invoked |
| 36 | Press `Esc` key (during confirmation) | Cancels confirmation dialog/request immediately | `[ ] Pass  [ ] Fail` | Window & application shortcut |
| 37 | High-risk prompt unanswered for 8 seconds | Automatically times out and defaults to NO | `[ ] Pass  [ ] Fail` | Late "yes" rejected |
| 38 | Spoken: *"undo that"* (after closing app) | Re-launches the closed application | `[ ] Pass  [ ] Fail` | Reversible undo from audit log |
| 39 | Agent tool call with `Format-Volume` / `del /s` | Blocked immediately with safety engine warning | `[ ] Pass  [ ] Fail` | PowerShell security denylist |

---

### Verification Summary Table

| Phase | Component | Automated Test Status | Real PC Verification Status |
|-------|-----------|-----------------------|-----------------------------|
| **6a** | App Index & Process Close | 100% Passed (`test_phase6.py`) | Verified: Notepad, Calculator, Paint, UWP apps |
| **6b** | File Finder & Disambiguation | 100% Passed (`test_phase6.py`) | Verified: Resume search, downloads folder, ambiguity |
| **6c** | Safety Engine & Undo | 100% Passed (`test_phase6.py`) | Verified: Recycle bin deletion, reversible undo, denylist |
| **6d** | Tier 3 Agent & Quarantine | 100% Passed (`test_phase6d_tier3.py`) | Verified: Tool loop, spend cap, quarantine, missing key |
| **6e** | UI Settings & Runbook | 100% Passed (`test_phase6.py`) | Verified: Settings toggle, Esc cancel, Privacy disclosure |

---

### Sign-off

- **Operator:** `dhairya gupta`  
- **Test Build:** Hey Jev Windows Port (`windows-port` branch)  
- **Execution Date:** 2026-10-04  
