# Hey Jev: Manual Acceptance-Testing Guide

**Target Environment:** Windows 10/11 x64  
**Repository Path:** `C:\Users\prabh\.gemini\antigravity\scratch\hey-jev`  
**Git Branch:** `windows-port`  
**Python Runtime:** Python 3.11.9 (`.venv`)  
**Purpose:** Step-by-step instructions for personal human operation and verification of Hey Jev without automated test surrogates.

---

## 1. Quick Reference & System Locations

### 1.1 Correct Commands to Launch the Desktop Application
Ensure your terminal is in the repository root (`C:\Users\prabh\.gemini\antigravity\scratch\hey-jev`).

**Primary Recommended Command:**
```powershell
.venv\Scripts\python.exe app.py
```
*(Equivalent CLI entry point: `.venv\Scripts\python.exe siri.py --ui`)*

### 1.2 Application Data Locations (Never Delete or Modify)
All persistent state resides in the Windows `%APPDATA%` directory:
- **Central Application Folder:** `C:\Users\prabh\AppData\Roaming\HeyJev`
  - `Hey Jev.log`: Real-time application log.
  - `actions.jsonl`: Security audit log and undo ledger.
  - `settings.json`: User interface and model preferences.
  - `vocabulary.json`: Custom phonetic pronunciation dictionary.
  - `apps.json`: Installed application aliases.
  - `memory.db`: Semantic context and memory store.
  - `projects.db`: Registered developer workspace registry.
  - `background_tasks.db`: Autonomous background jobs.
  - `heyjev_swarms.db`: Multi-agent swarm state.
- **Mission Persistence Database:** `C:\Users\prabh\AppData\Roaming\hey-jev\hey_jev_missions.db`
  - Authoritative SQLite database storing mission DAGs, task states, acceptance criteria, and audit events.

---

## 2. Feature Prerequisite & Configuration Matrix

| Feature / Subsystem | Operable Without External Keys? | Required Keys / Prerequisites | Notes |
| :--- | :--- | :--- | :--- |
| **GUI Startup & Navigation** | **YES** | None | Loads all 7 tabs instantly. |
| **Mission Center Window** | **YES** | None | Full DAG visualization, task graphs, criteria, controls. |
| **Mission Creation & Planning** | **YES** | None | Deterministic DAG planning via CapabilityRegistry. |
| **Mission Human Controls** | **YES** | None | Approve, pause, resume, cancel work out of the box. |
| **Restart Persistence** | **YES** | None | Relies on local SQLite WAL storage. |
| **OpenDots MCP Gateway** | **YES** | None (uses local Bearer token) | Discovers all 10 tools over loopback HTTP. |
| **Speech-to-Text (STT)** | **YES** | Local Whisper model (downloaded) | Runs locally via `faster-whisper` on CPU. |
| **Voice Decision Engine (Jev)** | **NO** | `TYPESAFE_API_KEY` | Needed for speculative voice System 1 intents. |
| **Spoken Voice Output (TTS)** | **NO** | `FISH_AUDIO_API_KEY` | Fish Audio cloud voice synthesis. |
| **Q&A Chit-Chat Answers** | **NO** | `OPENROUTER_API_KEY` | Haiku knowledge responses. |
| **OpenDots Conversational Chat** | **NO** | `INTELLIGENCE_API_KEY`, `OPENAI_API_KEY` | OpenDots browser chat requires CopilotKit + OpenAI. |

> [!NOTE]
> If voice API keys are not yet configured, the app will launch normally, switch to the **Keys** tab, and show the status `"Add your API keys to begin"`. You can still access all tabs and fully operate the Mission Center.

---

## 3. Step-by-Step Manual Acceptance Checks

---

### Test 1: Desktop Application Startup & Seven Main Tabs
Verify that the PySide6 desktop window launches cleanly and renders all seven tabs.

1. **Exact Action:**
   Open PowerShell and execute:
   ```powershell
   .venv\Scripts\python.exe app.py
   ```
2. **Expected Result:**
   - A dark-themed window titled **"Hey Jev"** appears (size ~500x560).
   - In the top header: A status dot appears next to the status label (`Starting`, `Ready`, or `Add your API keys to begin`).
   - Push-to-talk toggle buttons appear (`Hold Alt` and `Hey Jev`).
   - A row of **exactly seven tabs** is visible:
     1. **Home:** Shows example commands card and an **"Autonomous Missions"** card.
     2. **Dictionary:** Displays custom phonetic replacements (`vocabulary.json`).
     3. **Apps:** Table of application aliases and launch executables.
     4. **Dictation:** Table of past dictation transcripts.
     5. **Privacy:** Documents local data retention and telemetry policy.
     6. **Settings:** Controls for Decision Engine, Whisper models, push-to-talk keys, and diagnostics.
     7. **Keys:** Input fields for API keys stored in Windows Credential Manager.
   - A green circular status icon appears in the Windows System Tray (notification area).
3. **Signs of Failure:**
   - Window fails to open or crashes immediately with a traceback.
   - Tabs are missing, scrambled, or unclickable.
   - System tray icon is absent.

---

### Test 2: Opening the Mission Center
Verify that the Mission Center window opens from both the UI button and the system tray.

1. **Exact Action:**
   - **Method A:** Click the **Home** tab in the main window. Scroll down to the **Autonomous Missions** card and click the blue **"Open Mission Center"** button.
   - **Method B:** Right-click the **Hey Jev** icon in the Windows System Tray and select **"Mission Center…"**.
2. **Expected Result:**
   - A dedicated dark-themed window titled **"Mission Center — Hey Jev"** appears (minimum size 880x620).
   - The window has a top bar featuring:
     - Header: `🎯 Mission Center`
     - Blue **"＋ New Mission"** button
     - Gray **"⟳ Refresh"** button
   - The window is split into two panes:
     - **Left Pane:** Filter dropdown (`All Missions`, `Active`, etc.) and the mission overview table.
     - **Right Pane:** Five detail tabs:
       1. `Overview & Controls`
       2. `Tasks & Graph`
       3. `Acceptance Criteria`
       4. `Live Timeline`
       5. `Diagnosis & Health`
3. **Signs of Failure:**
   - Clicking "Open Mission Center" does nothing or displays an error dialog.
   - Window opens blank, or the splitter pane fails to render.

---

### Test 3: Defining an Objective & Inspecting Plan Decomposition
Verify the Mission Creation Dialog, objective normalization, and DAG preview without premature execution.

1. **Exact Action:**
   - In the Mission Center, click **"＋ New Mission"**.
   - In the **Create New Mission** dialog, enter the following harmless test objective in the text box:
     ```text
     Scan source directory and organize files in Downloads
     ```
   - Select the **COPILOT (Recommended)** radio button.
   - Click the **"Preview Plan"** button before creating.
2. **Expected Result:**
   - The **Plan Preview & Criteria** text box populates with a structured preview:
     - Goal normalized to: `scan source directory and organize files in downloads`.
     - Decomposition shows two dependency-linked tasks:
       - Step 1: `step_scan_folder` (`filesystem.list_directory`) — Low risk, read-only.
       - Step 2: `step_organize_items` (`filesystem.move_file`) — Medium risk checkpoint, depends on `step_scan_folder`.
     - Acceptance criteria are listed (identifying files, verifying movement).
   - No database records are created during preview (zero side-effects).
3. **Signs of Failure:**
   - "Preview Plan" button crashes with an unhandled exception.
   - Preview area remains empty or shows an invalid cycle error.

---

### Test 4: Creating a Mission & Verifying Task Graph & Acceptance Criteria
Verify mission persistence and multi-tab detail rendering.

1. **Exact Action:**
   - In the Create dialog with your previewed plan, click **"Create Mission"**.
   - In the Mission Center left pane, click on the newly created mission row.
   - Click each of the following detail tabs:
     - **Overview & Controls**
     - **Tasks & Graph**
     - **Acceptance Criteria**
2. **Expected Result:**
   - In **Overview & Controls**:
     - Status Badge shows **`[WAITING_FOR_APPROVAL]`** or **`[CREATED]`**.
     - Mode badge shows `Mode: COPILOT`.
     - An amber warning banner appears: `⚠ Approval checkpoint required before proceeding.`
   - In **Tasks & Graph**:
     - The table lists the decomposed tasks, their capability IDs, risk levels, and dependencies (`step_scan_folder` before `step_organize_items`).
   - In **Acceptance Criteria**:
     - Lists explicit verifiable conditions (e.g., target identification, file verification) with Status marked `PENDING`.
3. **Signs of Failure:**
   - The mission does not appear in the table on the left.
   - Tasks or acceptance criteria tabs show empty tables.
   - A mission in COPILOT mode starts executing high-risk steps without pausing for approval.

---

### Test 5: Approval Checkpoints and Execution Controls
Verify human control over mission progress and safe authorization.

1. **Exact Action:**
   - Select the mission in the Mission Center.
   - In **Overview & Controls**, observe that the **✓ Approve Plan** button is enabled.
   - Click **"✓ Approve Plan"**.
   - Click **"▶ Start / Execute"**.
2. **Expected Result:**
   - A confirmation message or status transition occurs.
   - The status badge transitions from `WAITING_FOR_APPROVAL` to `APPROVED` / `RUNNING`.
   - The amber alert banner clears.
   - In the **Tasks & Graph** tab, the first task (`step_scan_folder`) begins execution.
3. **Signs of Failure:**
   - Clicking "Approve Plan" fails silently or freezes the interface.
   - Execution proceeds on steps that required task-level approval without user confirmation.

---

### Test 6: Pause, Resume, and Cancellation Controls
Verify that running or pending missions can be paused, resumed, and cancelled safely.

1. **Exact Action:**
   - While a mission is active or awaiting approval, click the **"⏸ Pause"** button.
   - Observe the status badge and controls.
   - Next, click the **"⏯ Resume"** button.
   - Finally, click the red **"⏹ Cancel"** button.
2. **Expected Result:**
   - On **Pause**: Status badge updates to **`[PAUSED]`**. Progress pauses.
   - On **Resume**: Status badge returns to active/approved state.
   - On **Cancel**: A confirmation prompt appears. Once confirmed, status transitions to **`[CANCELLED]`**.
   - All control buttons update appropriately (e.g., Pause is disabled while paused).
3. **Signs of Failure:**
   - Status remains stuck in `RUNNING` after clicking Pause.
   - Cancel button fails to abort pending DAG tasks.

---

### Test 7: Event Timeline and Progress Bar Accuracy
Verify that lifecycle events and progress metrics update accurately.

1. **Exact Action:**
   - Select the mission in the Mission Center.
   - Click the **"Live Timeline"** tab.
   - Click the **"⟳ Refresh"** button in the top bar.
2. **Expected Result:**
   - The timeline table displays chronological events with timestamps:
     - `mission.created`
     - `plan.created`
     - `mission.status_changed` (e.g., transitions between `WAITING_FOR_APPROVAL`, `PAUSED`, `CANCELLED`)
   - Event types are color-coded (green for completion/approval, blue for started, amber for checkpoints).
   - In **Overview & Controls**, the progress bar accurately reflects completed tasks vs total tasks.
3. **Signs of Failure:**
   - Timeline table is completely blank despite status transitions having occurred.
   - Progress bar shows `100%` when tasks failed or were cancelled.

---

### Test 8: Persistence Across Application Restarts
Verify that missions and their exact states survive closing and reopening the application.

1. **Exact Action:**
   - Note the **Mission ID** and final status of your test mission (e.g., `CANCELLED` or `PAUSED`).
   - Close the Mission Center window.
   - Close the main Hey Jev window by pressing **Ctrl+Q** (or right-click tray icon -> **"Quit Hey Jev"**).
   - Verify in Task Manager or PowerShell that no orphan `python.exe` process is running:
     ```powershell
     Get-Process python -ErrorAction SilentlyContinue
     ```
   - Relaunch the application:
     ```powershell
     .venv\Scripts\python.exe app.py
     ```
   - Open the **Mission Center** again.
2. **Expected Result:**
   - The mission list on the left automatically loads the previous mission from `C:\Users\prabh\AppData\Roaming\hey-jev\hey_jev_missions.db`.
   - Clicking on the mission restores its exact title, plan, DAG tasks, acceptance criteria, timeline history, and final status.
3. **Signs of Failure:**
   - Mission list is empty after relaunch.
   - The mission status reverted to an earlier state or lost its task history.

---

### Test 9: OpenDots Gateway Connection & Tool Discovery (Optional)
Verify that the optional MCP gateway can start, bind to loopback, and enumerate Jev tools for external workspaces.

1. **Exact Action:**
   - Open a separate PowerShell window in the repository root.
   - Start the gateway on loopback with a test Bearer token:
     ```powershell
     $env:JEV_ENABLE_OPENDOTS_GATEWAY = "1"
     $env:JEV_OPENDOTS_PORT = "8765"
     $env:JEV_OPENDOTS_BEARER_TOKEN = "manual-test-token-42"

     .venv\Scripts\python.exe -c "from opendots_gateway import OpenDotsGatewayConfig, OpenDotsGatewayService, OpenDotsMCPGateway; import time; config = OpenDotsGatewayConfig.from_env(); gw = OpenDotsMCPGateway(config); svc = OpenDotsGatewayService(gw); svc.start(); print('Gateway bound to port', svc.bound_port); time.sleep(60)"
     ```
   - While it is running, open another PowerShell window and test tool discovery via curl:
     ```powershell
     $headers = @{ "Authorization" = "Bearer manual-test-token-42"; "Content-Type" = "application/json" }
     $body = '{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}'
     (Invoke-RestMethod -Uri "http://127.0.0.1:8765/mcp" -Method Post -Headers $headers -Body $body).result.tools | Select-Object name, description
     ```
2. **Expected Result:**
   - Gateway prints `Gateway bound to port 8765`.
   - The query returns **all 10 authoritative Hey Jev tools**:
     - `jev_health`
     - `jev_list_missions`
     - `jev_get_mission`
     - `jev_get_mission_timeline`
     - `jev_search_capabilities`
     - `jev_create_mission_preview`
     - `jev_submit_mission`
     - `jev_request_pause`
     - `jev_request_resume`
     - `jev_request_cancellation`
   - Zero approval-bypass tools are returned.
3. **Signs of Failure:**
   - Gateway fails to bind or errors with loopback violation.
   - Unauthorized requests without the Bearer token succeed (must return HTTP 401).

---

## 4. Collecting Logs and Reporting Issues Safely

If an unexpected behavior or failure occurs during your manual testing:

### 4.1 Viewing Real-Time Application Logs
The main application log is updated in real time at:
```powershell
Get-Content -Path "$env:APPDATA\HeyJev\Hey Jev.log" -Tail 50 -Wait
```

### 4.2 Safe Log Export (Secret Scrubbing)
Before sharing any logs, ensure that API keys and personal tokens are not present. Hey Jev already redacts recognized secrets using `sanitize_content`, but you can verify with:
```powershell
# Extract last 100 lines of log scrubbed of possible keys
Get-Content "$env:APPDATA\HeyJev\Hey Jev.log" -Tail 100 | ForEach-Object {
    $_ -replace "sk-[a-zA-Z0-9_-]{20,}", "[REDACTED_API_KEY]" `
       -replace "Bearer\s+[a-zA-Z0-9_-]+", "Bearer [REDACTED_TOKEN]"
}
```

### 4.3 Clean Exit
To cleanly close Hey Jev at any time:
- Press **Ctrl+Q** while the main window is focused.
- Or right-click the system tray icon -> select **"Quit Hey Jev"**.
