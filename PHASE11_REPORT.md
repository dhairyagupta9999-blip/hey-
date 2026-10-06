# Hey Jev — Phase 11 Engineering Report: Jev Agent Harness Foundation

**Repository:** Hey Jev (Windows Port)  
**Branch:** `windows-port`  
**Base Commit:** `cfa0131`  
**Phase:** Phase 11 Complete  
**Date:** October 2026  
**Hardware Profile:** Intel Core i3-5005U (2 physical cores / 4 logical threads @ 2.00 GHz), ~7.93 GB RAM, Intel HD Graphics 5500, Windows 10 Pro x64  
**Runtime:** Python 3.11.9  

---

## 1. Executive Summary & Objective

In Phase 11, Hey Jev undergoes a fundamental architectural evolution: **from a voice-command assistant to a general-purpose, autonomous agent orchestrator.**

Instead of executing only rigid, pre-programmed voice commands, Jev now possesses the foundation to receive high-level natural language goals:
* *"Organize my Downloads folder."*
* *"Find the best beachfront hotels in Australia."*
* *"Fix the error Antigravity just encountered."*
* *"Research this deeply and give me the best answer."*

Jev acts as the **central orchestrator**, responsible for:
1. Decomposing goals into dependency-aware task graphs (DAGs).
2. Deterministically routing subtasks to specialized agent providers based on capabilities, cost, and permissions.
3. Managing structured, normalized handoffs between heterogeneous agents (e.g. Antigravity -> Claude -> Verifier).
4. Persisting task state across application restarts in an atomic SQLite WAL database.
5. Enforcing a strict concurrency ceiling (`max_local_agents = 1`) to protect dual-core CPU performance.
6. Independently verifying agent results via deterministic checks rather than trusting self-reporting.

All existing Phase 0–10 voice capabilities (faster-whisper STT, Silero VAD, gated wake words, Fish Audio / Kokoro TTS, Tier 3 safety engine) remain completely intact and functional with **zero regressions**.

---

## 2. Core Architecture & System Topology

```
                         USER (Voice / Text)
                                 │
                                 ▼
                     Jev Voice / Conversation Loop
                                 │
                                 ▼
                         Jev Orchestrator
   ┌─────────────────────────────┼─────────────────────────────┐
   │                             │                             │
   ▼                             ▼                             ▼
Task Planner               Agent Router                 Task Storage (SQLite)
(DAG Decomposition)    (Capability Matching)       (WAL Mode, Restart Recovery)
   │                             │                             │
   └─────────────────────────────┼─────────────────────────────┘
                                 │
                                 ▼
                     Agent Provider Adapters
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
Antigravity Adapter       Claude / Reviewer        Tier 3 PC Control
(Real CLI / Artifacts)     (Analysis Provider)    (Windows MCP / System)
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │ (Structured Handoff)
                                 ▼
                         Independent Verifier
                     (Deterministic Evidence Check)
                                 │
                                 ▼
                         Verified Outcome
```

---

## 3. Detailed Component Implementations

### 3.1. Universal Task Model (`agent_task.py`)
Defines the provider-neutral `AgentTask` model with an explicit 9-state finite state machine:
* **Statuses:** `QUEUED`, `PLANNING`, `RUNNING`, `WAITING`, `BLOCKED`, `VERIFYING`, `COMPLETED`, `FAILED`, `CANCELLED`.
* **State Machine Invariant:** Transition between states is strictly validated by `transition_to()`. Illegal transitions (such as jumping from `QUEUED` directly to `COMPLETED`, or restarting a `CANCELLED` task) raise `InvalidStateTransitionError`.
* **Tracking & Context:** Every task maintains `task_id`, `parent_task_id`, `goal`, `priority`, `current_agent`, `assigned_agents`, `context`, `inputs`, `outputs`, `artifacts`, `errors`, `recommendations`, `next_action`, `confidence`, `requires_approval`, `approval_state`, `timestamps`, and `cancellation_state`.

### 3.2. Universal Agent Result Schema (`agent_result.py`)
Eliminates unstructured prose passing between agents by establishing `AgentResult`:
* **Fields:** `result`, `summary`, `error`, `files_changed`, `commands_run`, `questions`, `recommendations`, `confidence`, `next_action`, `artifacts`, `evidence`, `status`.
* **Inter-Agent Continuity:** When Agent A (e.g. Antigravity) encounters an issue, its `files_changed`, `recommendations`, and `next_action` are encapsulated in `AgentResult` and seamlessly handed off to Agent B (e.g. Claude Reviewer).

### 3.3. Provider-Neutral Adapter Interface (`agent_adapter.py`)
Establishes the abstract contract `AgentAdapter` that all providers must fulfill:
* `submit_task(task: AgentTask) -> bool`
* `get_task_status(task_id: str) -> TaskStatus`
* `stream_agent_output(task_id: str) -> Iterator[str]`
* `cancel_task(task_id: str, reason: str) -> bool`
* `get_task_result(task_id: str) -> Optional[AgentResult]`
* `approve_action(task_id: str, action_id: str, approved: bool) -> bool`
* Extension hooks: `health_check()`, `capabilities()`, `discover()`, `pause_task()`, `resume_task()`.

### 3.4. Antigravity Adapter (`antigravity_adapter.py`)
Directly integrates with the local Google DeepMind Antigravity environment without mock stubs:
* **CLI Detection:** Inspects `~/.gemini/antigravity/bin/agentapi.bat` and invokes subcommands (`get-conversation-metadata`, `new-conversation`, `send-message`).
* **Filesystem & Brain Inspection:** Interacts with the active brain directories in `~/.gemini/antigravity/brain/<conversation_id>/` (scratch tasks, markdown artifacts, `.system_generated/logs/transcript.jsonl`).
* **Normalized Output:** Extracts generated artifacts and files into `AgentResult` objects.

### 3.5. Agent Registry (`agent_registry.py`)
Maintains a dynamic registry of available agent providers, tracking:
* `name`, `provider`, `capabilities`, `supported_tasks`, `cost`, `speed`, `reliability`, `availability`, `permissions`, `health_status`, and bound adapter instances.
* Pre-seeded with baseline provider profiles: `antigravity`, `tier3_pc`, `claude`, `research_agent`, `windows_agent`.

### 3.6. Deterministic Agent Router (`agent_router.py`)
Implements capability- and rule-based deterministic routing:
* Maps coding/debugging tasks to `antigravity`.
* Maps review/critique tasks to `claude`.
* Maps web search/hotels/pricing tasks to `research_agent`.
* Maps UI/window/desktop control to `tier3_pc`.
* Maps file organization to `windows_agent`.
* Returns `RoutingDecision` with selected agent, fallbacks, confidence score, and rationale.

### 3.7. Task Decomposition & Graph Planner (`task_planner.py`)
Generates dependency-aware task graphs (`TaskGraph`):
* Models complex goals as Directed Acyclic Graphs (DAGs) supporting sequential, parallel, fan-out/fan-in, and parent/child hierarchies.
* Guarantees cycle prevention via `is_acyclic()` and raises `CyclicDependencyError` on circular references.
* Computes execution ordering via `topological_sort()`.

### 3.8. Structured Agent-to-Agent Handoff (`orchestrator.py`)
Coordinates multi-agent workflows using `HandoffEnvelope`:
* Carries context, results, recommendations, and execution history across agent transitions.
* Updates the parent task audit trail and emits `task.agent_handoff` events.

### 3.9. Persistent SQLite Storage (`task_storage.py`)
Provides durable task persistence in `%APPDATA%/HeyJev/tasks.db`:
* **WAL Mode:** Operates in `PRAGMA journal_mode=WAL` for high concurrency and crash resilience.
* **Tables:** `tasks`, `task_audit_log`, `task_results`.
* **Restart Recovery:** Recovers tasks that were interrupted in `RUNNING` or `PLANNING` states upon unexpected shutdown and safely re-queues them into `WAITING` status.

### 3.10. Internal Event Bus (`event_bus.py`)
Thread-safe pub/sub bus dispatching real-time lifecycle events:
* Events: `task.created`, `task.started`, `task.progress`, `task.waiting`, `task.agent_handoff`, `task.failed`, `task.retrying`, `task.completed`, `task.cancelled`, `task.verification_failed`.
* Supports wildcard subscriptions (e.g. `task.*`, `*`) and isolates handler exceptions.

### 3.11. Concurrency Governor & Resource Control
* Hardware-aware resource gating tailored to 2 CPU cores and 8 GB RAM.
* Strictly enforces `max_local_agents = 1` to prevent CPU exhaustion and ensure the real-time voice pipeline (STT, VAD, TTS) is never starved of CPU cycles.

### 3.12. Permissions Foundation (`PermissionPolicy`)
* Defines granular permission scopes: `READ_FILES`, `WRITE_FILES`, `EXECUTE_COMMAND`, `CONTROL_BROWSER`, `SEND_EMAIL`, `CHANGE_SETTINGS`, `INSTALL_SOFTWARE`, `NETWORK_ACCESS`.
* Enforces explicit gating: high-risk actions require human approval (`approval_state = APPROVED`), transitioning unapproved tasks to `BLOCKED`.

### 3.13. Deterministic Task Verifier (`task_verifier.py`)
* Enforces the critical rule: **Never trust an agent's self-report.**
* Verifies status flags, non-empty outputs, confidence thresholds, physical on-disk file existence for claimed file changes, and required artifact presence.

---

## 4. Verification Evidence & Test Results

### 4.1. Unit & Integration Test Suite (`test_phase11.py`)
All 20 test cases in `test_phase11.py` passed with 100% success rate:
1. `test_01_task_creation_and_defaults`: PASS
2. `test_02_explicit_state_machine_valid_transitions`: PASS
3. `test_03_invalid_state_transitions_raise_error`: PASS
4. `test_04_task_persistence_sqlite`: PASS
5. `test_05_restart_recovery_unfinished_tasks`: PASS
6. `test_06_task_graph_dag_and_cycle_prevention`: PASS
7. `test_07_task_decomposition_planner`: PASS
8. `test_08_task_cancellation_and_adapter_propagation`: PASS
9. `test_09_agent_registry_and_discovery`: PASS
10. `test_10_deterministic_agent_router`: PASS
11. `test_11_structured_agent_to_agent_handoff`: PASS
12. `test_12_result_normalization_and_serialization`: PASS
13. `test_13_event_bus_emission_and_wildcard_subscription`: PASS
14. `test_14_permissions_foundation_and_gating`: PASS
15. `test_15_concurrency_control_resource_governor`: PASS
16. `test_16_deterministic_task_verifier_separate_from_self_report`: PASS
17. `test_17_antigravity_adapter_integration`: PASS
18. `test_18_orchestrator_execute_task_success_lifecycle`: PASS
19. `test_19_orchestrator_execute_task_verification_failure`: PASS
20. `test_20_orchestrator_execute_task_permission_blocked`: PASS

### 4.2. Full Test Baseline Execution
```
======================= 214 passed in 211.64s (0:03:31) =======================
```
* **Baseline Tests:** 194 passed
* **Phase 11 Tests:** 20 passed
* **Total:** 214 passed, 0 failed, 0 skipped
* **Regressions:** Exactly 0

### 4.3. Preflight Truth Verification Run
```
======================================================================
PREFLIGHT SUMMARY: 11 pass, 0 fail, 3 warn
======================================================================
```
* STT engine warm: PASS (faster-whisper tiny.en, 2 CPU threads)
* TTS engine functional: PASS
* Safety engine: PASS (Win+R / Run-dialog blocked, PowerShell blocked)
* Secret scan: PASS (zero keys committed or logged)
* Real bug checks: PASS (dedup guard, loop breaker, exact app match)

---

## 5. Acceptance Criteria Checklist

| # | Acceptance Criterion | Status | Evidence / Verification |
| :--- | :--- | :--- | :--- |
| 1 | Universal `AgentTask` model with 9 explicit states | **DONE** | `agent_task.py`, `test_01`, `test_02`, `test_03` |
| 2 | Universal `AgentResult` schema and normalization | **DONE** | `agent_result.py`, `test_12` |
| 3 | Provider-neutral `AgentAdapter` interface | **DONE** | `agent_adapter.py` |
| 4 | Real `AntigravityAdapter` inspecting local env | **DONE** | `antigravity_adapter.py`, `agentapi.bat` verified, `test_17` |
| 5 | `AgentRegistry` with capability and cost metadata | **DONE** | `agent_registry.py`, `test_09` |
| 6 | Deterministic `AgentRouter` | **DONE** | `agent_router.py`, `test_10` |
| 7 | `TaskPlanner` with DAG dependency decomposition | **DONE** | `task_planner.py`, `test_06`, `test_07` |
| 8 | Structured Agent-to-Agent Handoff protocol | **DONE** | `orchestrator.py` (`HandoffEnvelope`), `test_11` |
| 9 | A2A / MCP Tech Radar Evaluation | **DONE** | `PHASE11_TECH_RADAR.md` |
| 10 | Persistent SQLite task database with WAL mode | **DONE** | `task_storage.py`, `test_04`, `test_05` |
| 11 | Internal `EventBus` with wildcard pub/sub | **DONE** | `event_bus.py`, `test_13` |
| 12 | Central task cancellation mechanism | **DONE** | `orchestrator.py` (`cancel_task`), `test_08` |
| 13 | Concurrency governor (`max_local_agents = 1`) | **DONE** | `orchestrator.py` (`ConcurrencyController`), `test_15` |
| 14 | Permissions foundation & safety gating | **DONE** | `orchestrator.py` (`PermissionPolicy`), `test_14`, `test_20` |
| 15 | Deterministic `TaskVerifier` independent of self-report | **DONE** | `task_verifier.py`, `test_16`, `test_19` |

---

## 6. Files Created / Modified

### Created Files:
* `agent_task.py` (Universal task model, explicit FSM, priorities, permissions)
* `agent_result.py` (Structured agent result schema and serialization)
* `agent_adapter.py` (Abstract provider adapter interface)
* `antigravity_adapter.py` (Concrete adapter for local Antigravity environment)
* `agent_registry.py` (Registry for agent providers, metadata, and capabilities)
* `agent_router.py` (Deterministic rule- and capability-based task router)
* `task_planner.py` (DAG task graph and goal decomposition planner)
* `task_storage.py` (SQLite persistent database with WAL mode and audit trail)
* `event_bus.py` (Thread-safe event bus with wildcard subscriptions)
* `task_verifier.py` (Independent deterministic verification engine)
* `orchestrator.py` (Master Jev orchestrator coordinating pipeline, handoff, and concurrency)
* `test_phase11.py` (Comprehensive 20-test test suite for Phase 11)
* `PHASE11_TECH_RADAR.md` (Standards and frameworks evaluation)
* `PHASE11_REPORT.md` (This engineering report)

---

## 7. Known Limitations & Future Extension Points (Phase 12+)

1. **Intelligent LLM Router (Phase 12+):** Current routing is deterministic and keyword/rule-based. In future phases, an LLM-assisted router can evaluate ambiguous or multi-step goals while retaining deterministic fallback.
2. **Dedicated QA Agent (Phase 13+):** The verifier currently executes deterministic checks (files changed, status flags, non-empty outputs). A dedicated reviewer agent (e.g. Claude / Codex) can be hooked into `TaskVerifier.register_validator()` for semantic code inspection.
3. **Voice UI Event Streaming (Phase 14+):** The `EventBus` provides hooks for real-time speech progress updates (e.g. *"Jev: I'm waiting for Claude's review"*). Connecting this to the PySide6 UI and TTS pipeline will be completed in UI/presence phases.
4. **Browser & GUI Autonomous Agents (Phase 15+):** Browser and full Windows GUI agents will implement `AgentAdapter` without altering the core orchestrator.
