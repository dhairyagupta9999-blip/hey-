# Phase 27: Architecture Gap Analysis
## Goal-Driven Autonomy, Persistent Missions and Adaptive Execution

### 1. Executive Summary & Context
In Hey Jev Phases 15 through 26.1, we implemented and hardened:
- Capability discovery, tool scanning, and skill registration (Phases 15 & 22)
- Workflow composition, interpolation, verification, and rollback reporting (Phases 22 & 22.1)
- Capability gap detection, extension proposals, isolated sandbox builds, staged runtimes, and health-verified deployment (Phases 23, 24, 24.1, 25, 25.1)
- Operational health observation, multi-signal failure diagnosis, and policy-governed autonomous maintenance (Phases 26 & 26.1)

However, until Phase 27, Hey Jev lacks an outcome-oriented, goal-driven **persistent mission subsystem**. Previously, Jev operated primarily on immediate single-workflow goals or event-triggered maintenance cycles. When a user presents a high-level, complex, or long-running objective requiring multi-stage coordination, uncertainty resolution, adaptive replanning across environmental shifts, and persistent recovery across application restarts, Jev had no durable mission state machine or bounded autonomous mission coordinator.

Phase 27 closes this gap without inventing parallel authorization paths or rebuilding established registries.

---

### 2. Analysis of Existing Architecture vs Phase 27 Requirements

| System Dimension | Existing State (Phases 15–26.1) | Phase 27 Requirement | Architecture Gap & Solution |
| :--- | :--- | :--- | :--- |
| **Mission State Representation** | Ephemeral `WorkflowPlan` (steps + DAG) and background `AgentTask`s. No multi-stage persistent mission entity. | First-class `Mission` model with lifecycle: `CREATED` $\to$ `PLANNING` $\to$ `READY` $\to$ `RUNNING` $\to$ `WAITING_FOR_APPROVAL` / `BLOCKED` $\to$ `VERIFYING` $\to$ `COMPLETED` / `PARTIALLY_COMPLETED` / `FAILED` / `NEEDS_ATTENTION`. | Build `mission_model.py` with explicit state transitions, strict validation rejecting illegal jumps, acceptance criteria, budgets, and constraints. |
| **Objective Interpretation** | Simple keyword/pattern matching in `WorkflowComposer` and `CapabilityDiscoveryEngine`. | Outcome-oriented natural language interpretation extracting normalized goals, explicit vs inferred constraints, measurable criteria, and uncertainty prompts. | Build `mission_interpreter.py` that leverages `CapabilityDiscoveryEngine`, distinguishes explicit vs inferred constraints, detects capability gaps, and prompts only on material ambiguities. |
| **Task Planning & DAG Decomposition** | `WorkflowComposer` constructs linear or simple branched step lists for immediate execution. | Dependency-aware DAG task planner with preconditions, timeouts, risk levels, and resource budgets suited for dual-core Windows. | Build `mission_planner.py` constructing topological DAG `MissionTask`s, checking cycles, respecting CPU/RAM bounds via `ResourceGovernor`. |
| **Task Execution & Gating** | `WorkflowEngine` executes `WorkflowPlan` with step-by-step approval and rollback. | Mission coordinator dispatching DAG tasks through `WorkflowEngine`, enforcing `MANUAL`, `COPILOT`, and `AUTOPILOT` policies. | Build `mission_coordinator.py` dispatching through existing `WorkflowEngine` and `CapabilityRegistry` without bypassing permission or audit gates. |
| **Adaptive Replanning** | Ad-hoc error recovery or rollback; no plan versioning, evidence preservation, or approval invalidation on scope expansion. | Controlled replanning engine with versioned plans, completed-task preservation, non-idempotent safety, and stale approval invalidation. | Build `mission_replanner.py` recording replan rationale, preserving verified completed work, checking budget limits, and requiring re-approval on material expansion. |
| **Persistence & Crash Recovery** | `SqliteMemoryStore` stores general memory records and incident logs; no dedicated schema for durable mission DAGs. | Durable SQLite store in WAL mode persisting missions, plans, tasks, evidence, and criteria with fresh-process restart recovery. | Build `mission_store.py` (`SqliteMissionStore`) with immediate transactions, WAL mode, foreign keys, and restart reconciliation. |
| **Verification & Truth** | Step verification criteria in `WorkflowStep`. | Observable acceptance criteria (`PASS`, `FAIL`, `BLOCKED`, `UNVERIFIED`) linked to verifiable evidence; distinct `COMPLETED` vs `PARTIALLY_COMPLETED`. | Formalize `AcceptanceCriterion` in `mission_model.py` and evaluation logic in `mission_coordinator.py`. |
| **Secret Sanitization** | `sanitize_content` applied to memory records and events. | Comprehensive sanitization across all mission prompts, task inputs/outputs, error traces, and persisted reports. | Enforce `sanitize_content` across all models and SQLite store operations. |

---

### 3. State Machine & Transition Rules

```
                      +-----------+
                      |  CREATED  |
                      +-----+-----+
                            | (start planning)
                            v
                      +-----+-----+
                      | PLANNING  |
                      +-----+-----+
                            | (plan generated)
                            v
                      +-----+-----+
         +----------->|   READY   |<-----------+
         |            +-----+-----+            |
         |                  |                  |
         |                  v                  |
         |            +-----+-----+            | (replan / resume)
         |     +----->|  RUNNING  |------+     |
         |     |      +-----+-----+      |     |
         |     | (approved) |            |     |
         |     |            v            |     |
(pause/  |     |  +--------------------+ |     |
 resume) |     +--|WAITING_FOR_APPROVAL| |     |
         |        +--------------------+ |     |
         |                  |            |     |
         |                  v            v     |
         |        +--------------------+ |     |
         |        |      BLOCKED       |-+     |
         |        +--------------------+       |
         |                  |                  |
         |                  v (tasks finished) |
         |            +-----+-----+            |
         |            | VERIFYING |            |
         |            +-----+-----+            |
         |                  |                  |
         |      +-----------+-----------+      |
         |      | (all req  | (partial) |(fail)|
         |      v  PASS)    v           v      |
         |  +-------+ +-----------+ +------+   |
         |  |COMPLET| | PARTIALLY | |FAILED|   |
         |  |  ED   | | COMPLETED | +------+   |
         |  +-------+ +-----------+            |
         +-------------------------------------+

Auxiliary States:
- PAUSED: from RUNNING / WAITING_FOR_APPROVAL -> can resume to READY or RUNNING
- CANCELLING -> CANCELLED: immediate abort of pending tasks, wait for in-flight tasks
- NEEDS_ATTENTION: ambiguous recovery state on restart, or unrecoverable non-idempotent task failure
```

#### Strict Transition Matrix
- Transitions not in the deterministic transition matrix raise `ValueError("Invalid mission state transition: {from} -> {to}")`.
- Terminal states: `COMPLETED`, `PARTIALLY_COMPLETED`, `CANCELLED`, `FAILED`. No transitions out of terminal states except explicit user-initiated restart/fork.

---

### 4. Human-in-the-Loop Modes (`MANUAL`, `COPILOT`, `AUTOPILOT`)

1. **`MANUAL`**:
   - Planner prepares the DAG and verifies preconditions.
   - Transitions to `READY`.
   - Never dispatches operational tasks autonomously. Waits for explicit user trigger for each task or batch.
2. **`COPILOT`**:
   - Autonomously executes safe / read-only tasks.
   - When encountering a task marked `approval_checkpoint=True`, `risk_level > RiskLevel.LOW`, or modifying environment state, transitions to `WAITING_FOR_APPROVAL`.
   - Pauses execution until explicit approval is granted for that specific task and plan version.
3. **`AUTOPILOT`**:
   - Executes pre-authorized tasks governed by explicit deterministic policy.
   - If an unapproved high-risk task, permission boundary, capability gap, or material ambiguity is encountered, it **halts and escalates** (`WAITING_FOR_APPROVAL` or `BLOCKED`).
   - Autopilot NEVER acts as blanket permission for arbitrary code execution, permission escalation, or unverified destructive operations.

---

### 5. Mission Budgets and Stop Conditions

Every mission enforces bounded resource allocation:
- `max_tasks`: Default 20. Prevents runaway decomposition loops.
- `max_decomposition_depth`: Default 3.
- `max_replan_attempts`: Default 3. Prevents endless replanning loops.
- `max_retries_per_task`: Default 2.
- `max_concurrent_tasks`: Default 1 on Windows dual-core Intel Core i3-5005U to avoid thread contention.
- `deadline_s`: Optional wall-clock mission timeout.

#### First-Class Stop Conditions:
1. **Goal Verified & Completed**: All required acceptance criteria evaluate to `PASS`. $\to$ `COMPLETED`.
2. **Partial Outcome Achieved**: Core tasks finished, non-critical criteria unmet or unverified. $\to$ `PARTIALLY_COMPLETED`.
3. **User Cancellation**: Cancellation requested; in-flight tasks recorded, pending cancelled. $\to$ `CANCELLED`.
4. **Approval Required**: High-risk task reached in Copilot/Autopilot without pre-approval. $\to$ `WAITING_FOR_APPROVAL`.
5. **Budget Exhausted**: Task limit, replan limit, or retry limit exceeded. $\to$ `FAILED` / `NEEDS_ATTENTION` with explicit reason.
6. **Capability Gap / Blocker**: Required capability missing or environment blocked. $\to$ `BLOCKED`.
7. **Policy Violation**: Prohibited or unauthorized action attempted. $\to$ `FAILED`.

---

### 6. Durable Persistence & Fresh-Process Restart Recovery

1. **Schema Design (`hey_jev_missions.db`)**:
   - `missions`: stores mission metadata, objective, normalized goal, status, budget, constraints, mode, approval records.
   - `mission_plans`: stores plan version, replan rationale, timestamps.
   - `mission_tasks`: stores task DAG nodes, dependencies, capability ID, status, inputs, outputs, verification method, evidence, error, idempotency flag.
   - `mission_acceptance_criteria`: stores criteria text, required flag, status (`PASS`, `FAIL`, `BLOCKED`, `UNVERIFIED`), evidence.
2. **Crash Recovery Algorithm**:
   - On runtime startup (`JevRuntime.start()`):
     - Identify missions in non-terminal states (`RUNNING`, `PLANNING`, `VERIFYING`, `CANCELLING`).
     - Query in-flight tasks marked `RUNNING`.
     - Check side effects:
       - If task was read-only/idempotent: reset to `PENDING` with retry count incremented.
       - If task had side effects or ambiguous outcome: mark `NEEDS_ATTENTION` to prevent unsafe duplicate execution.
     - Move interrupted mission to `NEEDS_ATTENTION` or `READY` depending on whether ambiguity exists.
     - Revalidate permissions and plan integrity. Stale approvals from prior process lifetimes are NOT implicitly trusted if the plan requires re-evaluation.

---

### 7. Adaptive Replanning Safety Invariants

When an unexpected event occurs (capability failure, tool error, constraint change):
1. **Preserve Completed Work**: All tasks with status `COMPLETED` and valid verification evidence remain preserved in the new plan version.
2. **Identify Impacted Subgraph**: Only downstream dependent or failed tasks are replanned or rescheduled.
3. **Detect Material Expansion**:
   - If the new plan introduces new capabilities, increases risk levels, or requests new permissions, `plan.materially_changed = True`.
   - Any prior approval granted to the previous plan version is **invalidated**.
   - Mission transitions to `WAITING_FOR_APPROVAL`.
4. **Enforce Replan Budget**:
   - `current_replan_count += 1`.
   - If `current_replan_count > max_replan_attempts`, abort replanning and fail mission with `BudgetExhaustedError`.

---

### 8. Verification Strategy & Acceptance Criteria Classification

Instead of relying on LLM assertions:
- **`PASS`**: Measurable physical or programmatic evidence collected matching acceptance criteria (e.g., file exists, checksum matches, test passes, health check returns healthy).
- **`FAIL`**: Evidence indicates outcome was not achieved (e.g., non-zero exit code, assertion failed).
- **`BLOCKED`**: Prerequisite unmet or required capability missing.
- **`UNVERIFIED`**: Task completed execution, but physical verification was inconclusive or dependent on untestable external systems.
- A mission CANNOT transition to `COMPLETED` if any required criterion is `FAIL`, `BLOCKED`, or `UNVERIFIED`. If optional criteria remain unverified, it transitions to `PARTIALLY_COMPLETED`.

---

### 9. Secret Sanitization & Audit Trails

- All user inputs, goals, constraints, task inputs, outputs, error messages, and final reports must pass through `sanitize_content` from `memory_model.py`.
- No Google API tokens, passwords, bearer credentials, or sensitive file contents are persisted in SQLite or emitted to `EventBus`.

---

### 10. Implementation Plan Summary

1. `mission_model.py`: Models, enums, acceptance criteria, budgets, state transitions.
2. `mission_store.py`: `SqliteMissionStore` with WAL mode, transaction safety, crash recovery queries.
3. `mission_interpreter.py`: Goal parsing, constraint separation, criteria synthesis, ambiguity questioning.
4. `mission_planner.py`: DAG planning, topological sort, cycle detection, budget checks.
5. `mission_replanner.py`: Adaptive plan revisions, completed task preservation, approval invalidation.
6. `mission_coordinator.py`: Master orchestrator, mode enforcement, event deduplication, verification.
7. `runtime_manager.py`: Wire `SqliteMissionStore` and `MissionCoordinator` into `JevRuntime`.
8. `test_phase27.py`: Comprehensive 20-scenario test suite matching prompt requirements.
