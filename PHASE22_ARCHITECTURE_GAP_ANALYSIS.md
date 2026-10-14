# Phase 22 Architecture & Gap Analysis: Capability Discovery, Intelligent Tool Selection & Workflow Composition

**Date:** October 10, 2026  
**Branch:** `windows-port`  
**Baseline Commit:** `087b2c380e7ac859197dd7b25f8c3025683264b8`  

---

## 1. Executive Summary

Phase 22 elevates Hey Jev from an agent executing pre-configured commands or hardcoded skill templates into an autonomous system capable of:
1. **Understanding Outcome-Oriented Goals:** Reasoning about the *desired result* (e.g. "Organize messy downloads and set the 4th photo as wallpaper") rather than requiring users to memorize concrete tool or skill IDs.
2. **Unified Capability Discovery & Ranking:** Searching a normalized index of both primitive tools and composite skills using deterministic BM25 and multi-factor metadata scoring, with explainable candidate selection.
3. **Dependency-Aware Workflow Composition:** Dynamically assembling discovered capabilities into a directed acyclic graph (DAG) with data-pipelining between steps, preconditions, verification checks, and approval gates.
4. **Governed Execution Modes:** Supporting **Manual**, **Copilot**, and **Autopilot** execution modes without elevating permissions, violating security boundaries, or overloading hardware.
5. **Independent Outcome Verification:** Distinguishing true physical/observable outcome achievement from a tool call that merely returned without raising an exception.

---

## 2. Existing Infrastructure & Reuse Assessment

| Subsystem | Existing Component | Current Role | Phase 22 Reuse Strategy |
| :--- | :--- | :--- | :--- |
| **Tool Registry** | `tool_registry.py`, `tool_model.py` | Stores concrete tools, health status, and metadata schemas. | **Reuse directly.** Wrap tools into the unified `CapabilityDefinition` without breaking legacy registry APIs. |
| **Skill Registry** | `skill_registry.py`, `skill_model.py` | Stores version-pinned multi-step skill workflows. | **Reuse directly.** Adapt skills as composite capabilities with declared inputs, outputs, and requirements. |
| **Matchers** | `tool_matcher.py`, `skill_matcher.py` | Scores tools by capability string and skills by regex patterns. | **Extend & Unify.** Bridge into `CapabilityDiscoveryEngine` supporting outcome-based semantic matching. |
| **Task DAG** | `task_planner.py` (`TaskGraph`) | Provides DAG acyclicity, dependency resolution, topological sorting. | **Reuse core DAG algorithms.** Embed in `WorkflowComposer` for deterministic dependency sequencing. |
| **Swarm & Gateway**| `swarm_planner.py`, `provider_gateway.py` | Role allocation, provider routing, cost-first dispatch. | **Reuse gateway safely.** Maintain strict separation; discovery must NOT report an LLM healthy if rate-limited. |
| **Verification** | `verification_engine_v2.py`, `task_verifier.py` | Multi-level physical evidence checking ("A tool return is NOT proof of success"). | **Integrate.** Use evidence verification at every workflow step checkpoint. |
| **Safety & Approval**| `safety_engine.py` | Command blocking, Run-dialog blocks, approval gates. | **Reuse & Enforce.** High/critical risk actions require approval in Copilot mode and are blocked in Autopilot mode. |
| **Audit & Memory** | `event_bus.py`, `memory_store.py` | PubSub events and local SQLite WAL persistent memory. | **Integrate.** Record workflow decisions to `EventBus` and durable outcomes to `SqliteMemoryStore`. |

---

## 3. Identified Architecture Gaps

1. **Fragmented Capability Abstraction:** Tools and Skills have distinct schemas (`ToolDefinition` vs `SkillDefinition`). No common model exists to describe outcomes, required arguments, reversibility, observable success criteria, and verification strategies uniformly.
2. **Hardcoded Goal Routing:** Existing planners rely on hardcoded keyword lists or domain regexes (`organize downloads`, `fix bug`). An unfamiliar or compound user request fails to discover or combine multi-domain capabilities.
3. **Absence of Dynamic Workflow Synthesis:** While skills declare static internal steps (`SkillStep`), the system cannot dynamically link outputs of one capability into inputs of another (e.g. `filesystem.list_directory` -> select image -> `windows.set_wallpaper`) to fulfill novel goals.
4. **Lack of Formal Execution Modes:** Hey Jev previously executed either inline or as detached background tasks without standard user-governed modes (`Manual` review, `Copilot` supervised execution, `Autopilot` policy-bounded autonomy).
5. **Missing Information & Ambiguity Handling:** Matchers previously returned top-k candidates even when required arguments were missing or when multiple competing plans had materially different side-effects.

---

## 4. Phase 22 Architecture Design

```
+----------------------------------------------------------------------------+
|                             USER GOAL INPUT                                |
|       "Find my newest downloaded picture and make it my desktop wallpaper"  |
+----------------------------------------------------------------------------+
                                      |
                                      v
+----------------------------------------------------------------------------+
|                        CAPABILITY DISCOVERY ENGINE                         |
|  - Unified registry: ToolDefinition + SkillDefinition -> CapabilityDef     |
|  - Deterministic BM25 & metadata scoring against desired outcomes          |
|  - Health & availability filtering (offline-first, no heavy local models)  |
|  - Missing requirement detection & ambiguity assessment                    |
+----------------------------------------------------------------------------+
                                      |
                                      v
+----------------------------------------------------------------------------+
|                             WORKFLOW COMPOSER                              |
|  - Dependency DAG construction (topological ordering)                      |
|  - Data-pipelining ($step1.output.picture_path -> $step2.input.image_path) |
|  - Precondition checks & observable verification criteria                  |
|  - Risk assessment & Approval checkpoints assignment                       |
+----------------------------------------------------------------------------+
                                      |
                                      v
+----------------------------------------------------------------------------+
|                              WORKFLOW ENGINE                               |
|  - Execution Modes: MANUAL (preview only), COPILOT (prompt for gated steps)|
|                     AUTOPILOT (strict policy bounds only)                  |
|  - Step execution with timeout and bounded retries (<= 2)                  |
|  - Physical outcome verification (file exists, state changed)              |
|  - Rollback & recovery metadata propagation                                |
+----------------------------------------------------------------------------+
                                      |
                                      v
+----------------------------------------------------------------------------+
|                          OBSERVABILITY & MEMORY                            |
|  - Emits events to EventBus (audit trail)                                  |
|  - Stores verified lessons/outcomes in SqliteMemoryStore                   |
+----------------------------------------------------------------------------+
```

---

## 5. Security, Hardware & Performance Safeguards
* **Zero Credential Touches:** Zero modifications to Google plugin authentication or configuration files.
* **Deterministic Non-LLM Core:** Discovery, ranking, and workflow assembly execute via pure Python (sub-10ms latency on dual-core CPU), requiring zero GPU/LLM dependencies.
* **Non-Escalation Guarantee:** Low-privilege workflows can never elevate their own permissions or bypass `SafetyEngine` approval boundaries.
* **Physical Truth Verification:** Workflows verify actual disk/system changes rather than accepting empty or unverified return codes.
