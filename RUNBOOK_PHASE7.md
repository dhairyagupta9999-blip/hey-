# Phase 7 Acceptance Runbook: Windows MCP Automation Integration

This runbook documents the acceptance testing procedures for **Phase 7 Step 2 (Windows MCP Client Integration)**.

---

## 1. Automated Test Suite (Mocks Only)

> [!IMPORTANT]
> All automated tests in `test_phase7_mcp.py` strictly mock the MCP server over stdio and external LLM APIs. **No automated test sends real keystrokes, typing, or clicks into the user's active desktop window.**

Run the complete suite:
```powershell
.venv\Scripts\pytest.exe -v test_phase7_mcp.py
```
Expected result: **21 passed**.

---

## 2. Real PC Acceptance Commands

### Read-Only Inspection Commands (Zero Clicks, Zero Typing)
Run these three safe, read-only `--text` commands on your real PC to verify end-to-end routing without typing into active windows:

#### Command 1: Open Window Listing (Read-only UI Inspection)
```powershell
.venv\Scripts\python.exe siri.py --text "list the open windows"
```
- **What it does:** Discovers and enumerates top-level visible desktop windows.
- **Safety level:** `SAFE` (read-only inspection).
- **Audit proof:** Check `%APPDATA%\HeyJev\actions.jsonl` for `"action": "list_windows"` or `"window_management"`, `"risk_level": "SAFE"`.

#### Command 2: File Discovery (Read-only Search)
```powershell
.venv\Scripts\python.exe siri.py --text "find files named resume"
```
- **What it does:** Searches local indexed user directories for matching filenames.
- **Safety level:** `SAFE` (read-only query). Output quarantined within `<DATA>` / `<MCP_DATA>` tags.
- **Audit proof:** Check `%APPDATA%\HeyJev\actions.jsonl` for `"action": "find_files"`, `"risk_level": "SAFE"`.

#### Command 3: Active Window Status (Read-only State Inspection)
```powershell
.venv\Scripts\python.exe siri.py --text "what window is currently active"
```
- **What it does:** Inspects the current foreground window title without sending mouse clicks or keystrokes.
- **Safety level:** `SAFE` (read-only).
- **Audit proof:** Check `%APPDATA%\HeyJev\actions.jsonl` for `"risk_level": "SAFE"`.

### Input Commands with Preview Mode ON (Prompt Before Action)
With the Settings toggle **"Confirm every AI-agent UI action"** enabled (`tier3_confirm_all_actions: True`, default ON), run these two input commands:

#### Command 4: Open Notepad and Type Hello World
```powershell
.venv\Scripts\python.exe siri.py --text "open Notepad and type hello world"
```
- **What it does:**
  1. Opens Notepad (safe app launch).
  2. Performs foreground window check verifying active window is Notepad (blocks shells, admin tools, Run dialog).
  3. **Preview Mode Prompt:** Speaks *"I am about to type 'hello world'. Should I proceed?"*
  4. Waits 8 seconds for explicit user "yes" confirmation (defaults to NO after 8s).
  5. When confirmed with "yes", dispatches typing.
- **Safety level:** `MEDIUM` / Preview Mode Confirmed.
- **Audit proof:** Check `%APPDATA%\HeyJev\actions.jsonl` for `"type_text"` / `"ui_type"`.

#### Command 5: Close Notepad
```powershell
.venv\Scripts\python.exe siri.py --text "close Notepad"
```
- **What it does:** Safely locates and closes the Notepad process/window with recorded undo capability.
- **Safety level:** `MEDIUM` (undoable).
- **Audit proof:** Check `%APPDATA%\HeyJev\actions.jsonl` for `"action": "close_app"`, `"target": "Notepad"`, `"undoable": true`.

---

## 3. Spoken Acceptance Test Matrix (12 Cases)

| # | Spoken Phrase | Expected System Behavior | Result | Category |
| :-: | :--- | :--- | :-: | :--- |
| **1** | *"What windows are currently open?"* | Discovers open top-level windows via UI inspection; speaks back summary | `[ ] Pass  [ ] Fail` | Read / Inspection |
| **2** | *"Find my budget spreadsheet"* | Searches user filesystem; quarantines results in `<DATA>` tags | `[ ] Pass  [ ] Fail` | Read / Inspection |
| **3** | *"Take a screenshot of the active window"* | Captures window bounds using `screenshot_control`; returns state | `[ ] Pass  [ ] Fail` | Read / Inspection |
| **4** | *"Open Notepad"* | Tier 2 / App index launches Notepad; logs as `SAFE` | `[ ] Pass  [ ] Fail` | App Launch |
| **5** | *"Type Hello World into Notepad"* | Gated as `MEDIUM` risk; speaks back action and executes typing | `[ ] Pass  [ ] Fail` | UI Action (Typing) |
| **6** | *"Press Ctrl plus S"* | Gated as `MEDIUM` risk; dispatches keystrokes via `keyboard_control` | `[ ] Pass  [ ] Fail` | UI Action (Keys) |
| **7** | *"Click the Save button"* | Gated as `MEDIUM` risk; semantically identifies button by UIA name | `[ ] Pass  [ ] Fail` | UI Action (Click) |
| **8** | *"Kill process chrome.exe"* | Blocked immediately by default; `process` tool denied; logged as `BLOCKED` | `[ ] Pass  [ ] Fail` | Denylist (Process) |
| **9** | *"Run PowerShell command Get-Process"* | Blocked by default from MCP stdio; requires Tier 3 confirmed PowerShell path | `[ ] Pass  [ ] Fail` | Denylist (Shell) |
| **10** | Spoken *"stop"* during multi-step turn | Instantly signals global cancel; halts remaining steps; speaks *"Action cancelled."* | `[ ] Pass  [ ] Fail` | Cancellation Hook |
| **11** | Press `Esc` key during in-flight operation | Signals global cancellation; stops MCP tool execution; speaks *"Action cancelled."* | `[ ] Pass  [ ] Fail` | Keyboard Cancel |
| **12** | MCP Server missing or unavailable | Speaks single clean message: *"Windows automation service is unavailable..."*; stays off | `[ ] Pass  [ ] Fail` | Error & Resilience |

---

## 4. Verification Record: Real PC vs. Mocks

| Item | Verification Mode | Evidence |
| :--- | :--- | :--- |
| **Standalone Binary Install** | **Real PC** | Downloaded `windows-mcp-server-1.3.27-win-x64.zip` without GitHub token; extracted `Sbroenne.WindowsMcp.exe` to `.venv-agents\bin\mcp-windows\`; verified on disk. |
| **Real Size on Disk** | **Real PC (Measured)** | `59,899,787 bytes` (`57.12 MB`). |
| **Network Egress / Telemetry** | **Real PC (Measured)** | Process monitored via `psutil`: `len(p.net_connections()) == 0` and 0 child processes. Zero outbound connections. |
| **MCP Handshake & Discovery** | **Real PC & Mock** | Real PC: stdio handshake with `Sbroenne.WindowsMcp.exe` queried and discovered all 18 tools. Unit tests: mocked stdio pipes verify handshake and allowlist filtering. |
| **Tool Allowlist & Denylist** | **Mocks** | Tested in `test_phase7_mcp.py`: permitted tools pass; `process`, `file_save`, `powershell`, and unknown tools rejected immediately. |
| **Safety Engine Risk Gating** | **Mocks & Local** | `evaluate_risk()` verified: typing/keys/clicks = `MEDIUM`, read/inspect = `SAFE`, unknown/denied = `BLOCKED`. |
| **Prompt Injection Quarantine** | **Mocks** | Tested with injection payloads: stripped and enclosed within `<MCP_DATA>` tags. |
| **8-Step Ceiling & 30s Timeout** | **Mocks** | Tested in `test_phase7_mcp.py`: loop terminates at step 8 ceiling; 30s timeout aborts cleanly without freezing. |
| **Global Cancellation (Esc / Stop)**| **Mocks & UI** | Tested in `test_phase7_mcp.py` and UI shortcut in `assistant_ui.py`: `request_global_cancel()` halts in-flight operations. |
| **Foreground Shell / Admin Block**| **Mocks & Local** | Verified in `test_phase7_mcp.py`: PowerShell, cmd, wt, regedit, taskmgr, Run dialog, KeePass, Bitwarden, 1Password blocked from UI interaction. |
| **Prohibited Key Combinations**| **Mocks & Local** | Verified in `test_phase7_mcp.py`: `Win+R`, `Win+X`, `Ctrl+Shift+Esc`, `Ctrl+Alt+Del`, `Win+Pause` blocked outright. |
| **Window Closing Confirmation**| **Mocks & Local** | Verified in `test_phase7_mcp.py`: `Alt+F4` and `Ctrl+W` require explicit "yes" confirmation. |
| **Sensitive Login / Banking Protection**| **Mocks & Local** | Verified in `test_phase7_mcp.py`: windows with login, password, or banking titles require explicit "yes" confirmation. |
| **Preview Mode (Prompt-before-action)**| **Mocks & Settings UI**| Verified in `test_phase7_mcp.py` and `assistant_ui.py`: Speaks preview sentence and awaits "yes" before sending any click, type, or key to MCP. Defaults to ON. |
| **Live App Path MCP Call**| **Real PC** | Invoked `window_management` (find) and `ui_snapshot` via `mcp_client` in Python: verified live stdio response and `<MCP_DATA>` wrapping with 0 real clicks or keystrokes. |
