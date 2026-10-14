# Hey Jev: Testing & Validation Playbook

This document describes how to execute, verify, and validate Hey Jev across automated test suites, live preflight checks, and manual acceptance testing.

---

## 1. Test Environment Requirements

- **Operating System:** Windows 10 or Windows 11 x64 (Native Windows, not WSL or Linux container).
- **Python Runtime:** Python 3.11 or 3.12 located in `.venv`.
- **Audio Output & Input:** Working Windows audio device (speaker/headphones and microphone) configured in Windows Sound settings.
- **Node.js (for MCP Client Tests):** Node.js v18+ with `npm` (required only for Phase 29.2 OpenDots MCP SDK integration tests).
- **Working Directory:** All commands must be run from the repository root:
  `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev`

---

## 2. Automated Test Execution Commands

### 2.1 Complete Repository Regression Suite
Runs all 46 Python test files (832 total unit and integration tests):
```powershell
.venv\Scripts\pytest.exe -q
```
- **Expected Result:** `832 passed in ~346s` (0 failed, 0 skipped).
- **Execution Time:** ~5 to 6 minutes.

### 2.2 Live Environment Preflight Suite
Executes real hardware and environment checks against the host machine without mocks:
```powershell
.venv\Scripts\python.exe preflight.py
```
- **Expected Result:** `PREFLIGHT SUMMARY: 13 pass, 0 fail, 1 warn`.
- **Acceptable Warning:** `MCP server binary exists but failed handshake` (occurs because the standalone `windows-mcp.exe` external server is not running).
- **Passing Checks:**
  1. App components accessible
  2. STT engine warm and transcribes audio
  3. TTS cache hit or synthesis
  4. Jev decision backend functional
  5. Answer provider fallback handling
  6. Tier 2 resolves known app without launching (Notepad)
  7. Memory engine functional (SQLite WAL)
  8. Safety engine strictly blocks Run dialog and Win+R
  9. Secret scan: zero API keys detected in disk logs
  10. Loop breaker trips on 3 identical firings
  11. Dedup guard drops duplicate transcript within 5s
  12. Exact app match prioritized
  13. Shell process `powershell.exe` strictly blocked

### 2.3 OpenDots MCP Gateway Test Suites
Tests the standalone MCP gateway over loopback HTTP:
```powershell
# Python gateway test suite (Phase 29 & 29.1):
.venv\Scripts\pytest.exe test_phase29.py test_phase29_1.py -v

# Official OpenDots Node.js MCP SDK integration suite (Phase 29.2):
.venv\Scripts\python.exe run_opendots_node_audit.py
```
- **Expected Result for Node audit:** 46 checks passed, 0 failed.

### 2.4 Targeted Phase Test Suites
When developing or modifying specific subsystems, run individual phase test files to save time:
```powershell
# Core Actions & UI (Phases 1-3)
.venv\Scripts\pytest.exe test_phase1.py test_phase2.py test_phase3.py

# Tier 2 Resolvers & Safety Engine (Phase 6)
.venv\Scripts\pytest.exe test_phase6.py test_phase6d_tier3.py

# Computer Operator 2.0 (UIA) & Watchers (Phases 20-21)
.venv\Scripts\pytest.exe test_phase20.py test_phase21.py test_phase21_1.py test_phase21_2.py

# Mission System & Mission Center (Phases 27-28)
.venv\Scripts\pytest.exe test_phase27.py test_phase27_1.py test_phase28.py

# Data Integrity & Cleanup Safety (Phase 29.2.1)
.venv\Scripts\pytest.exe test_data_integrity_cleanup.py
```

---

## 3. Automated Test Categories

1. **State Machine & Protocol Tests (`test_phase1*.py` - `test_phase10.py`):**
   Verify turn state transitions, Push-to-Talk lifecycle, hotkey handlers, audio segment queueing, and VAD endpointing.
2. **Action & Resolver Tests (`test_phase2.py`, `test_phase6*.py`):**
   Verify Win32 action dispatch, process tree termination, volume scalar calculations, Start Menu fuzzy matching, and window placement.
3. **Safety Engine & Security Tests (`test_phase6c.py`, `test_data_integrity_cleanup.py`):**
   Verify risk classification (`SAFE` to `CRITICAL`), blocked command execution, secret redaction, and user confirmation gating.
4. **Agent Harness & Subsystem Tests (`test_phase11.py` - `test_phase26*.py`):**
   Verify agent task routing, consensus protocols, SQLite WAL memory persistence, and dynamic tool discovery.
5. **Mission DAG & Progress Tests (`test_phase27*.py`, `test_phase28.py`):**
   Verify topological sorting, cyclic dependency detection, task status propagation, criteria evaluation, and progress calculation.
6. **MCP Gateway Protocol Tests (`test_phase29*.py`):**
   Verify Streamable HTTP transport, JSON-RPC 2.0 formatting, session ID generation, Bearer token authentication, and tool execution.

---

## 4. Manual End-to-End Testing

For comprehensive, beginner-friendly human verification instructions, refer to:
**[MANUAL_TEST_GUIDE.md](file:///C:/Users/prabh/.gemini/antigravity/scratch/hey-jev/MANUAL_TEST_GUIDE.md)**

### Quick Manual Smoke Test Checklist:
1. **Desktop App Startup:**
   ```powershell
   .venv\Scripts\python.exe app.py
   ```
   - Window opens showing the 7 tabs.
   - Status Tab indicates `"Ready"` or prompts for keys.
   - System tray icon is visible in Windows taskbar tray.
2. **Mission Center Launch:**
   - In the GUI, switch to the **Missions** tab and click **"Open Mission Center"**.
   - A dedicated window opens showing the mission list, DAG canvas, and criteria table.
3. **Create & Approve Test Mission:**
   - Click **"New Mission"** and enter a harmless goal (e.g., `"Verify system status and memory health"`).
   - Click **"Plan Mission"**. Verify the DAG canvas draws task nodes.
   - Click **"Approve & Execute"**. Verify task nodes transition from Pending -> Running -> Completed.
4. **Persistence Across Restart:**
   - Close the application (`Ctrl+C` or close window).
   - Re-launch `.venv\Scripts\python.exe app.py` and open Mission Center.
   - Verify the previous mission is still listed with its completed status and history intact.

---

## 5. Verification Gaps & Untested Behaviors

The following critical desktop behaviors lack automated end-to-end test coverage and require human verification:

| Behavior | Why It Lacks Automated Test Coverage | Risk / Failure Mode |
| :--- | :--- | :--- |
| **Microphone Audio Quality & Background Noise** | Automated tests feed clean synthetic float32 arrays or test clips. Real room acoustics, microphone gain, and background speech differ significantly. | Whisper STT misrecognizes words (e.g. acoustic confusion) or drops quiet commands. |
| **Real UIA Focus Stealing** | Unit tests run against mock or isolated windows. In real Windows usage, popups, system notifications, or UAC prompts can steal focus. | UIA click or keypress lands in the wrong window. |
| **Visible Browser Navigation (Non-Headless)** | CDP automation tests run in `--headless=new` mode with mock or temporary profiles. | Web pages open invisibly in the background; extensions and saved logins are unavailable. |
| **Real Cloud Voice Latency (Fish Audio / TypeSafe)** | Test runs use local caches or mocks with 0ms delay. | Live voice experience has 2–3s combined latency, making conversational flow feel sluggish. |
| **Windows DPI Scaling Multi-Monitor** | Tests run on a single virtual display geometry. | UIA coordinate calculations may miss target elements on secondary displays with different DPI scaling (e.g., 125% or 150%). |
