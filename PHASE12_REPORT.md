# Hey Jev — Phase 12 Engineering Report: Jev Windows Computer Operator

**Repository:** Hey Jev (Windows Port)  
**Branch:** `windows-port`  
**Base Commit:** `e3ecce7`  
**Phase:** Phase 12 Complete  
**Date:** October 2026  
**Hardware Profile:** Intel Core i3-5005U (2 physical cores / 4 logical threads @ 2.00 GHz), ~7.93 GB RAM, Intel HD Graphics 5500, Windows 10 Pro x64  
**Runtime:** Python 3.11.9  

---

## 1. Executive Summary & Philosophy

In Phase 12, Hey Jev transitions from task planning into **autonomous Windows execution**. Following Jev's core philosophy, the system receives **high-level natural language goals** rather than rigid voice commands:
* *"Open the fourth picture."*
* *"Change my wallpaper."*
* *"Turn dark mode on."*
* *"Organize my Downloads folder."*
* *"Open Calculator and close it."*
* *"Run PowerShell to inspect system state."*

Jev determines **HOW** to accomplish the goal using a **Semantic-UI-First, Native-First** architecture. Rather than relying on brute-force coordinate-clicking or expensive vision models that would overwhelm the dual-core 8 GB CPU, Jev interacts with the operating system via:
1. **Windows UI Automation (UIA) & COM Accessibility Tree** (`pywinauto` backend="uia")
2. **Native Win32 APIs** (`user32`, `shell32`, `advapi32`)
3. **Structured PowerShell Tooling** (policy-gated, non-interactive)
4. **Selective High-Resolution Screenshots** (on-demand verification only)

Every mutating operation strictly executes the **OBSERVE → DECIDE → ACT → OBSERVE AGAIN → VERIFY** loop. Actions never claim success merely because a process return code was zero; success requires independent verification of actual desktop and filesystem state changes.

---

## 2. Computer Operator Architecture & Topology

```
                         USER (Voice / Text)
                                 │
                                 ▼
                     Jev Voice / Conversation Loop
                                 │
                                 ▼
                         Jev Orchestrator
                                 │
                                 ▼
                           Agent Router
                                 │ (Routes computer_use / windows_ui)
                                 ▼
                       WindowsComputerAdapter
                     (Implements AgentAdapter ABC)
                                 │
                                 ▼
                       WindowsComputerTools
     ┌───────────────────────────┼───────────────────────────┐
     ▼                           ▼                           ▼
Semantic UIA Engine      Native Win32 APIs        Safe Filesystem & PowerShell
(pywinauto controls)    (user32 / wallpaper /    (preview mode, rollback metadata,
                         window management)       timeout-bounded execution)
     │                           │                           │
     └───────────────────────────┼───────────────────────────┘
                                 │
                                 ▼
                  OBSERVE → ACT → VERIFY Loop
                                 │
                                 ▼
                       ComputerActionResult
                    (Verified evidence & state)
```

---

## 3. Detailed Component Deliverables

### 3.1. Windows Computer Tools (`windows_computer_tools.py`)
A comprehensive, provider-neutral toolkit exposing:
* **Screen Observation:**
  * `observe_screen()`: returns desktop resolution, active window, and visible window list.
  * `take_screenshot(path)`: selective on-demand screen capture via `Pillow`.
* **Window Management:**
  * `get_active_window()`, `list_windows()`, `find_window(query)`.
  * `focus_window()`, `minimize_window()`, `maximize_window()`, `restore_window()`, `close_window()`.
  * `wait_for_window(query, timeout)`.
* **Semantic UI Automation:**
  * `get_ui_tree(hwnd_or_query, max_depth)`: parses control hierarchy via `pywinauto` UIA backend.
  * `find_ui_element(locator)`: semantic lookup by `name`, `control_type`, `automation_id`, `partial_text`, `index`.
* **Application Control:**
  * `open_application(app_target)`: resolves aliases, URI protocols, and executables (`calc.exe`, `notepad.exe`, `spotify:`, `chrome.exe`).
  * `close_application(app_name, force)`: graceful window close followed by process termination.
* **Safe Filesystem Engine:**
  * `list_directory()`, `find_files()`, `get_file_metadata()`, `open_file()`, `copy_file()`, `move_file()`, `create_directory()`.
  * **Protected Deletion:** `delete_file()` requires explicit human approval; unapproved requests return `needs_approval=True` and a preview summary.
  * **Dry-Run Preview:** `move_file(preview=True)` calculates proposed paths without touching the disk.
* **Structured PowerShell:**
  * `execute_powershell(command, timeout)`: runs `-NoProfile -ExecutionPolicy Bypass` under strict timeout ceilings with blocked token sanitization.
* **Settings & Wallpaper:**
  * `get_wallpaper()`, `set_wallpaper(path)`: applies wallpaper via `SystemParametersInfoW(SPI_SETDESKWALLPAPER)` and verifies change.
  * `get_dark_mode()`, `set_dark_mode(enabled)`: updates registry theme keys with `WM_SETTINGCHANGE` broadcast.
* **Observe → Act → Verify Result:**
  * Emits `ComputerActionResult` with `before_state`, `after_state`, `verified`, `verification_summary`, `rollback_metadata`, `confidence`.

### 3.2. Windows Computer Provider Adapter (`windows_computer_adapter.py`)
Binds `WindowsComputerTools` directly to Phase 11's `AgentAdapter` interface:
* `submit_task()`, `get_task_status()`, `stream_agent_output()`, `cancel_task()`, `get_task_result()`, `approve_action()`.
* Maps natural language goals to execution flows:
  * *"Open the fourth picture"*: scans Pictures directory, filters images, sorts, selects index 3, opens, and verifies viewer window.
  * *"Change my wallpaper"*: reads image, validates, sets wallpaper, verifies desktop state.
  * *"Turn dark mode on"*: observes theme state, toggles, verifies.
  * *"Organize my Downloads"*: analyzes files, categorizes into Images, Documents, Archives, Installers, Code; supports preview mode or execution with rollback tracking.
  * *"Open Calculator and close it"*: launches, verifies window presence, gracefully closes, verifies process termination.

### 3.3. Task Planner Integration (`task_planner.py`)
Extended `TaskPlanner.decompose_goal()` with DAG computer planning patterns:
* **Open nth picture:** `inspect_dir` → `filter_images` → `select_item` → `open_image` → `verify_window`.
* **Wallpaper change:** `locate_image` → `validate_image` → `apply_wallpaper` → `verify_wallpaper`.
* **Organize directory:** `scan_dir` → `categorize` → `preview_moves` → `execute_moves` → `verify_organization`.
* **App lifecycle:** `launch_app` → `wait_readiness` → `close_app` → `verify_closed`.

### 3.4. Agent Registry & Router Integration (`agent_registry.py`, `agent_router.py`)
* Registered `windows_agent` with full capabilities: `computer_use`, `windows_ui`, `filesystem`, `application_control`, `window_management`, `settings`, `wallpaper`, `powershell`, `observation`, `verification`, `dry_run`.
* Router matches computer tasks (`wallpaper`, `dark mode`, `powershell`, `organize`, `downloads`) deterministically to `windows_agent`.

### 3.5. Safety & Permissions Policy
* Operations categorized into `LOW_RISK` (reads, observation), `MEDIUM_RISK` (app launch, focus, wallpaper, move files), and `HIGH_RISK` (settings modification, command execution, file deletion).
* `PermissionPolicy` gates `CHANGE_SETTINGS` and `EXECUTE_COMMAND` behind explicit approval states (`approval_state == 'APPROVED'`).

---

## 4. Real-PC Demonstrations & Verification Evidence

All demonstration tests were executed against real Windows 10 OS subsystems using dedicated sandbox fixtures (`%TEMP%\HeyJev\phase12-test`):

### DEMO 1: "Open the fourth picture"
* **Setup:** Generated 5 distinct test image files (`photo_01.png` to `photo_05.png`) in sandbox.
* **Goal:** *"Open the fourth picture"*
* **Execution:** Adapter inspected sandbox, filtered images, sorted lexicographically, selected item #4 (`photo_04.png`), invoked default viewer, and verified execution.
* **Verification:** Target correctly identified as `photo_04.png`, `res.result['index'] == 4`, `status == SUCCESS`.

### DEMO 2: "Change my wallpaper" (with observe-act-verify & restore)
* **Setup:** Read current wallpaper via `SystemParametersInfoW`, created solid test image.
* **Goal:** *"Change my wallpaper"*
* **Execution:** Validated file existence, applied wallpaper via Win32 API with desktop refresh broadcast, queried wallpaper path again to verify change.
* **Cleanup:** Restored original user wallpaper to leave system pristine.
* **Verification:** `res.verified == True`, `status == SUCCESS`, original wallpaper verified restored.

### DEMO 3: Application Open and Close Lifecycle
* **Goal:** *"Open Notepad and close it"*
* **Execution:** Launched process, polled window list until window was visible, sent `WM_CLOSE`, polled process list until termination verified.
* **Verification:** `res.result['lifecycle'] == 'opened_and_closed'`, `closed_verified == True`.

### DEMO 4: "Organize test files" (Preview first, then execute)
* **Setup:** Populated sandbox with mixed files (`report.pdf`, `vacation.jpg`, `setup.exe`, `script.py`, `archive.zip`).
* **Preview Step:** Task run with `preview=True`. System proposed 5 categorized destinations (`Documents/report.pdf`, `Images/vacation.jpg`, `Installers/setup.exe`, `Code/script.py`, `Archives/archive.zip`) without altering disk. Root files verified intact.
* **Execute Step:** Task run with `preview=False`. Subdirectories created, files moved, destinations verified on disk.
* **Verification:** All 5 destination files confirmed on disk, root directory clean, rollback metadata captured.

---

## 5. Test Suite & Preflight Metrics

### 5.1. Phase 12 Test Suite (`test_phase12.py`)
* **Total Tests:** 15 passed, 0 failed, 0 skipped in 9.19s.
* **Coverage:** Adapter registration, deterministic routing, screen observation, window controls, filesystem CRUD, protected deletion, PowerShell execution & timeout, ComputerActionResult, dry-run preview, task decomposition, and Demos 1–4.

### 5.2. Full System Test Suite
```
======================= 229 passed in 196.03s (0:03:16) =======================
```
* **Phase 0–10 Baseline:** 194 passed
* **Phase 11 Harness:** 20 passed
* **Phase 12 Windows Operator:** 15 passed
* **Total:** **229 passed, 0 failed, 0 skipped** (Zero regressions)

### 5.3. Preflight Truth Verification
```
======================================================================
PREFLIGHT SUMMARY: 12 pass, 0 fail, 2 warn
======================================================================
```
* STT engine warm: PASS (faster-whisper tiny.en, 9132 ms)
* TTS engine functional: PASS (Kokoro fallback + cache)
* MCP server handshake: PASS
* Safety engine: PASS (Win+R / Run-dialog blocked, PowerShell blocked)
* Secret scan: PASS (zero keys committed or logged)
* Real bug checks: PASS (dedup guard, loop breaker, exact app match)

---

## 6. Resource Impact & Hardware Budget

* **Target CPU:** Intel Core i3-5005U (2 cores / 4 threads @ 2.00 GHz).
* **RAM Overhead:** `WindowsComputerTools` introduces near-zero persistent memory overhead (<10 MB). `pywinauto` UIA connections are transient.
* **CPU Impact:** Native Win32 operations execute in <5 ms. PowerShell executions are timeout-bounded (15s ceiling).
* **Voice Pipeline Protection:** Concurrency ceiling (`max_local_agents = 1`) guarantees that computer automation never contends with faster-whisper STT or Kokoro TTS.

---

## 7. Files Created & Modified

### Created Files:
* `windows_computer_tools.py` (Unified Windows computer tooling: UIA, Win32, filesystem, PowerShell, settings, wallpaper, observe-act-verify loop)
* `windows_computer_adapter.py` (WindowsComputerAdapter connecting computer tools to Phase 11 AgentAdapter)
* `test_phase12.py` (15 comprehensive unit, lifecycle, and real-PC sandbox tests)
* `PHASE12_TECH_RADAR.md` (Technology evaluation of Windows computer use tools)
* `PHASE12_REPORT.md` (This engineering report)

### Modified Files:
* `agent_registry.py` (Registered windows_agent with computer_use capabilities and default adapter binding)
* `agent_router.py` (Deterministic routing rules for computer_use goals)
* `task_planner.py` (DAG goal decomposition for picture, wallpaper, organize, and app lifecycle goals)
* `task_storage.py` (Ensured persistent connection for in-memory SQLite instances)

---

## 8. Known Limitations & Future Extension Points (Phase 13+)

1. **Browser DOM Automation (Phase 13+):** Phase 12 automates desktop applications and the Windows shell. Deep browser DOM navigation (Playwright/CDP) will be introduced in the browser operator phase.
2. **Dedicated Visual QA Agent (Phase 13+):** Screenshots are currently captured selectively for verification. Future multimodal agents will perform visual reasoning over complex graphical canvases.
3. **Rollback Time-Machine Engine (Phase 14+):** Phase 12 captures `rollback_metadata` for every mutating action (source, destination, previous settings). A dedicated `undo_action(action_id)` manager will be layered over this metadata.

---

**Strict Stop:** Phase 12 is complete and verified on `windows-port`. Awaiting your signal to proceed to the next phase.
