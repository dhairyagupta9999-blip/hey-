# PHASE 28 ARCHITECTURE GAP ANALYSIS: MISSION CENTER, LIVE PROGRESS VISUALIZATION AND HUMAN CONTROL

**Branch**: `windows-port`  
**Base Commit**: `5f57a5f` (Phase 27.1 Report)  
**Code Baseline**: `d162fc4` (Phase 27.1 Implementation)  
**Regression Status**: 763 passed, 0 failed, 0 skipped  
**Preflight Status**: 13 passed, 0 failed, 1 warning  

---

## 1. Executive Summary

Phase 27 and Phase 27.1 delivered the foundational backend for goal-driven autonomous missions:
- Decomposing natural-language objectives into verifiable task DAGs (`MissionInterpreter`, `MissionPlanner`).
- Managing execution via `WorkflowEngine`, approval checkpoints, adaptive replanning, and resource governance (`MissionCoordinator`, `MissionReplanner`).
- Persisting state and audit evidence with crash recovery in SQLite WAL (`SqliteMissionStore`).
- Integrating with operational health, diagnosis, and autonomous recovery (`OperationalMaintenanceCoordinator`).

However, **Hey Jev currently lacks any user interface for missions**. The existing native desktop application (`assistant_ui.py`) is restricted to the 7 original voice assistant tabs (Home, Dictionary, Apps, Dictation, Privacy, Settings, Keys). Long-running missions execute purely via programmatic Python APIs without visual inspection, live progress tracking, interactive approval controls, or visible task DAGs.

Phase 28 bridges this gap by building a native Windows **Mission Center** integrated into Hey Jev's PySide6 desktop application, providing transparent observability, live event-driven progress updates, and safe human control.

---

## 2. Existing Desktop UI & Runtime Inspection

### 2.1 UI Framework & Threading Conventions
- **Framework**: `PySide6` (Qt 6 for Python) with native Windows 11 dark aesthetics (Segoe UI, #0f172a slate background, #1e293b surfaces, #38bdf8 sky blue accents).
- **Threading Model**: 
  - PySide6 enforces strict single-thread UI access: all GUI widgets must only be created and manipulated on the Qt main thread.
  - Background workers communicate with the UI exclusively through `QObject` signals (`WorkerSignals`).
  - `assistant_ui.py` uses `queue.Queue` (`controls_queue`) and worker threads for voice processing.
- **Tab Layout Invariant**: `test_phase3.py` explicitly asserts that `MainWindow.tabs.count() == 7` and checks the exact list of tab titles: `["Home", "Dictionary", "Apps", "Dictation", "Privacy", "Settings", "Keys"]`. Adding an 8th tab to `MainWindow.tabs` by default would break baseline Phase 3 tests.

### 2.2 Subsystem Integration Points in `JevRuntime`
`JevRuntime` already initializes and wires:
- `self.mission_store`: SQLite persistence at `<appdata>/hey_jev_missions.db`.
- `self.mission_coordinator`: Main execution coordinator managing tasks, approvals, pauses, and cancellations.
- `self.mission_interpreter`: Natural language interpretation and constraint extraction.
- `self.mission_planner`: DAG construction and dependency resolution.
- `self.mission_replanner`: Adaptive replanning upon capability failure.
- `self.event_bus`: Wildcard event bus (`mission.*` events emitted during mission lifecycle).
- `self.maintenance_coordinator`: Operational health, failure diagnosis, and incident memory.
- `self.governor`: Resource governor slot allocation.

---

## 3. Architecture Gap Analysis

| Capability Required in Phase 28 | Current Baseline State | Identified Architectural Gap | Proposed Solution |
|---|---|---|---|
| **Mission Overview Dashboard** | Only programmatic query via `store.list_missions()` | No UI display of mission list, statuses, blockers, or progress. | Build `MissionOverviewWidget`: list/table with status badges, progress bars, active filters, and last update times. |
| **Mission Detail View** | `Mission` dataclass serializable to dict | No UI inspection of objective, normalized goal, constraints, budget, or report. | Build `MissionDetailWidget`: structured card layout displaying metadata, constraints, remaining budget, and final report. |
| **Task Dependency Graph** | Tasks have `dependencies: List[str]` in DAG | No visual task list showing topological order, dependencies, risk, and verification status. | Build `MissionTaskGraphWidget`: dependency-aware task tree/list displaying topological execution order, prerequisites, capabilities, outputs, and errors. |
| **Acceptance Criteria Verification** | `AcceptanceCriterion` with PASS/FAIL/BLOCKED/UNVERIFIED | No visual display distinguishing completed execution from verified outcomes. | Build `AcceptanceCriteriaWidget`: dedicated status cards showing criteria, verification evidence, external dependencies, and timestamps. |
| **Live Event Timeline** | `EventBus` publishes `mission.*` events | Events are discarded if no programmatic listener is registered; no UI timeline. | Build `MissionTimelineWidget`: bounded event feed displaying lifecycle events, correlating mission, task, and plan version without duplicates. |
| **UI-Thread Event Marshalling** | EventBus calls handlers on worker threads | Directly updating widgets from EventBus causes Qt cross-thread exceptions or crashes. | Build `MissionCenterSignals(QObject)`: EventBus listener emits Qt signals, which marshal updates safely to the main GUI thread. |
| **Human Mission Controls** | Programmatic methods on `MissionCoordinator` | No UI buttons for Start, Pause, Resume, Cancel, Approve Plan, Approve Task, Replan. | Build `MissionControlBar`: state-aware action buttons with confirmation dialogs, duplicate click suppression, and version-bound approvals. |
| **Mission Creation Experience** | `runtime.create_mission()` exists | No user-facing dialog to input objective, select mode, and preview proposed plan. | Build `MissionCreateDialog`: natural language input, mode selector (MANUAL/COPILOT/AUTOPILOT), plan preview, and capability validation. |
| **Failure Diagnosis & Recovery** | Phase 26 maintenance coordinator and incident memory | Incidents and diagnoses are logged to memory store but not visible in UI. | Build `MissionDiagnosisWidget`: shows symptoms, verified causes vs hypotheses, recovery attempts, and incident details with secret scrubbing. |
| **Application Integration** | Standalone 7-tab `MainWindow` | User cannot launch or navigate to Mission Center from desktop app. | Add "Open Mission Center" action on Home tab and System Tray menu, launching dedicated `MissionCenterWindow` while preserving 7-tab baseline. |

---

## 4. Component Architecture & Design

### 4.1 UI Component Hierarchy
```
MissionCenterWindow (QMainWindow)
│
├── Top Toolbar
│   ├── Filter Selector (All, Active, Waiting Approval, Paused, Blocked, Completed, Failed)
│   ├── Create Mission Button
│   ├── Refresh Button
│   └── System Health & Governor Status Indicator
│
├── Split View (QSplitter: Left 38%, Right 62%)
│   ├── Left Pane: Mission Overview List (QScrollArea / QTableWidget)
│   │   ├── Mission Summary Cards / Table
│   │   └── Empty State View ("No missions yet. Create your first mission.")
│   │
│   └── Right Pane: Tabbed Mission Workspace (QTabWidget)
│       ├── Tab 1: "Overview & Controls"
│       │   ├── Mission Header (Objective, Mode, Status Badge, Progress Bar)
│       │   ├── Action Control Bar (Start, Pause, Resume, Cancel, Approve, Replan)
│       │   ├── Blocker / Approval Required Warning Banner
│       │   └── Metadata & Constraints Box (Normalized goal, budget, permissions)
│       │
│       ├── Tab 2: "Tasks & Graph" (MissionTaskGraphWidget)
│       │   ├── Topological Task Hierarchy
│       │   ├── Prerequisites & Capability Bindings
│       │   └── Inputs, Outputs, and Error Drawer
│       │
│       ├── Tab 3: "Acceptance Criteria" (AcceptanceCriteriaWidget)
│       │   ├── Criteria list with PASS/FAIL/BLOCKED/UNVERIFIED badges
│       │   └── Verification Evidence & External Dependencies
│       │
│       ├── Tab 4: "Live Timeline" (MissionTimelineWidget)
│       │   ├── Correlated Event History (Created, Started, Approved, Blocked, Verified)
│       │   └── Duplicate Event Suppression
│       │
│       └── Tab 5: "Diagnosis & Recovery" (MissionDiagnosisWidget)
│           ├── Observed Failures & Symptoms
│           ├── Verified Causes vs Hypotheses
│           └── Recommended Recovery Actions & Incident Records
```

### 4.2 Threading & Synchronization Architecture
1. **Event Ingestion**:
   - `MissionCenterWindow` subscribes to EventBus patterns: `mission.*`, `deployment.*`, `health.*`.
   - The EventBus handler receives `TaskEvent` on an arbitrary background worker thread.
   - The handler immediately emits a Qt signal: `signals.mission_event_received.emit(event)`.
2. **Main Thread Dispatch**:
   - Qt's signal-slot mechanism automatically marshals the event to the main GUI thread via `Qt.QueuedConnection`.
   - The slot updates UI widgets, appends to the timeline, and triggers targeted item refreshes.
3. **Authoritative State Reconciliation**:
   - UI widgets never calculate speculative progress counters in response to events alone.
   - Progress is computed directly from authoritative `Mission.acceptance_criteria` and `MissionPlan.tasks` records.
   - If an event is missed or after an action executes, the UI reloads authoritative state via `store.get_mission(mission_id)`.
4. **Action Execution**:
   - Clicking an action button (e.g., "Start Mission" or "Approve Checkpoint") executes the coordinator method in a background worker thread (`QThread` or `threading.Thread`) to keep the desktop UI 100% responsive.
   - The button is disabled immediately upon click to prevent race conditions or duplicate submissions.
   - Upon completion, the worker signals the main thread with results to update the UI.

### 4.3 Security & Secret Scrubbing
- All displayed strings (objectives, goals, constraints, task inputs, outputs, errors, reasons, incident symptoms) pass through `sanitize_content` before being rendered.
- No raw API keys, bearer tokens, or private secrets can ever appear in the interface.

---

## 5. Implementation Roadmap

1. **`mission_ui_model.py` / `mission_ui_signals.py`**:
   - Qt signals (`MissionCenterSignals`) for cross-thread event dispatching.
   - Presentation helpers for status badge formatting, progress calculation, and duration formatting.
2. **`mission_create_dialog.py`**:
   - PySide6 dialog for natural language objective input, mode selection, plan preview, and submission.
3. **`mission_center.py` (`MissionCenterWindow` & widgets)**:
   - Complete Mission Center desktop window implementing Overview, Detail, Task Graph, Acceptance Criteria, Live Timeline, and Diagnosis.
4. **Integration with `assistant_ui.py` & `runtime_manager.py`**:
   - Add "Open Mission Center" button on `MainWindow`'s Home tab and System Tray menu.
   - Maintain 100% backwards compatibility with `test_phase3.py` (7 tabs invariant preserved).
5. **Phase 28 Test Suite (`test_phase28.py`)**:
   - Comprehensive test suite covering all 20 required scenarios in Section 11 of the prompt.
6. **Full Regression Verification & Preflight**:
   - Run complete repository regression suite ($\ge 763$ passed).
   - Run `preflight.py` (13 pass, 0 fail, 1 warn).
7. **Implementation Report**:
   - Document decisions and results in `PHASE28_IMPLEMENTATION_REPORT.md`.
