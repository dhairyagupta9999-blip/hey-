# Phase 23 Architecture & Gap Analysis: Safe Capability Acquisition, Integration Discovery and Extension Proposals

**Date:** October 10, 2026  
**Branch:** `windows-port`  
**Baseline Commit:** `c0f8278ec429b6e2550a80ba2f6b407140d56ea1`  

---

## 1. Executive Summary

Phase 22 established outcome-oriented capability discovery and dynamic DAG workflow composition, while Phase 22.1 hardened runtime execution, policy enforcement across all three modes (`MANUAL`, `COPILOT`, `AUTOPILOT`), safe input resolution, accurate rollback tracking, and physical verification.

Phase 23 tackles the next evolutionary boundary: **what happens when Hey Jev encounters a goal for which no valid, verified capability currently exists?**

The governing principle of Phase 23 is:
> **Jev can discover, propose, prepare, and validate new capabilities. It cannot silently authorize itself to install arbitrary code, access new private data, or expand its own privileges.**

Phase 23 enables Jev to:
1. **Accurately Analyze Capability Gaps:** Inspect failed workflows, unachievable goals, or missing prerequisites, cleanly distinguishing genuinely missing capabilities from unavailable tools, denied permissions, ambiguous requests, or safety-disallowed actions.
2. **Prioritize Integration & Capability Sourcing:** Search for solutions in order of safety: existing registered tools $\rightarrow$ supported integrations/adapters $\rightarrow$ configured local integrations $\rightarrow$ trusted package catalogs $\rightarrow$ external candidates with full provenance tracking.
3. **Model Structured Extension Proposals:** Maintain formal, reviewable proposals (`ExtensionProposal`) tracking goal context, provenance, requested permissions, risks, rollback plans, and measurable acceptance criteria.
4. **Safely Validate Extensions in Isolation:** Perform multi-stage bounded validation (metadata verification, static inspection, dependency checks, synthetic isolated execution) without executing untrusted arbitrary code or exposing credentials.
5. **Strictly Enforce Approval & Permission Boundaries:** Maintain cryptographic separation between discovery, proposal, validation, approval, installation, and activation. Re-approval is mandatory if candidate metadata, dependencies, or permissions materially mutate.
6. **Integrate with Existing Architecture:** Plug cleanly into `CapabilityRegistry`, `WorkflowEngine`, `SkillExecutor`, `SafetyEngine`, `EventBus`, `SqliteMemoryStore`, and `ResourceGovernor` without rebuilding or duplicating existing components.

---

## 2. Existing Infrastructure & Reuse Strategy

| Existing Subsystem | Location | Current Role | Phase 23 Reuse & Integration Strategy |
| :--- | :--- | :--- | :--- |
| **Capability Registry** | `capability_registry.py`, `capability_model.py` | Unified capability index with metadata, risk levels, and permissions. | **Target for Activation:** Approved and verified extensions register as active capabilities here. |
| **Workflow Engine** | `workflow_engine.py`, `capability_workflow.py` | DAG workflow synthesis, approval gates, bounded retries, and verification. | **Source of Gaps:** Supplies failed execution reports and unplannable goals to the gap analyzer. |
| **Tool & Skill Systems** | `tool_registry.py`, `skill_registry.py`, `skill_executor.py` | Stores primitive tools and multi-step skill definitions. | **First Priority for Sourcing:** Gap analyzer searches these before looking externally. |
| **Safety Engine** | `safety_engine.py` | Validates commands against blacklists and high-risk restrictions. | **Validation Constraint:** Extension validator checks proposed tools against safety rules. |
| **Resource Governor** | `governor.py` | CPU, memory, and concurrency limit enforcement. | **Sandbox Enforcement:** Bounded validation subprocesses are constrained by the governor. |
| **Event Bus** | `event_bus.py` | PubSub decoupled event delivery with secret sanitization. | **Audit Trail:** Emits structured proposal lifecycle events (`proposal.created`, `proposal.validated`, etc.). |
| **Persistent Memory** | `memory_store.py`, `memory_model.py` | SQLite WAL persistent memory for decisions and learnings. | **Durable Audit:** Records proposals, evaluations, approvals, and failure lessons. |

---

## 3. Identified Architecture Gaps

1. **Absence of Systematic Gap Taxonomy:** When a goal fails or cannot be composed into a workflow, Jev currently reports a generic failure. It cannot distinguish between:
   - A tool that exists but was not discovered
   - A tool that exists but is temporarily unhealthy or degraded
   - A missing user permission (which must NOT be solved by installing software)
   - A missing configuration parameter or credential
   - A genuinely missing capability
   - An ambiguous or contradictory user request
   - An unsafe or policy-blocked request
2. **No Tiered Candidate Sourcing with Provenance:** There is no mechanism to investigate potential integrations across local adapters, supported provider integrations, and trusted catalogs while tracking author, version, dependencies, license, and trust scores.
3. **Lack of a Formal Proposal Lifecycle:** No data model exists to represent an extension proposal across its lifecycle (`DETECTED` $\rightarrow$ `INVESTIGATING` $\rightarrow$ `PROPOSED` $\rightarrow$ `VALIDATING` $\rightarrow$ `AWAITING_APPROVAL` $\rightarrow$ `APPROVED`/`REJECTED` $\rightarrow$ `INSTALLING` $\rightarrow$ `VERIFYING` $\rightarrow$ `ACTIVE`/`FAILED`), preventing human oversight and auditability.
4. **Absence of Bounded Extension Validation:** No isolated pipeline exists to inspect candidate extensions statically, verify schema compliance, evaluate dependencies, and run non-executing or synthetic tests before activation.
5. **Separation of Concerns between Discovery, Validation, and Authorization:** Previous code paths risked conflating discovering a tool with authorizing its execution. A strict architectural firewall must ensure discovery, proposal, and validation grant zero runtime permissions.

---

## 4. Phase 23 Architecture Design

```
+--------------------------------------------------------------------------------------------------+
|                                    FAILED GOAL / UNPLANNABLE INTENT                              |
|                           "Analyze this SQLite database and export PDF report"                   |
+--------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+--------------------------------------------------------------------------------------------------+
|                                   CAPABILITY GAP ANALYZER                                        |
|  - Classifies gap into GapType:                                                                  |
|    * EXISTING_UNDISCOVERED (Suggest better query/alias)                                          |
|    * UNAVAILABLE_OR_UNHEALTHY (Surface health repair)                                            |
|    * MISSING_PERMISSION (Prompt for permission grant; NO tool install)                          |
|    * MISSING_CONFIGURATION (Propose config setup; redact secrets)                                |
|    * GENUINELY_MISSING (Initiate candidate discovery)                                            |
|    * AMBIGUOUS_REQUEST (Request clarification)                                                   |
|    * SAFETY_DISALLOWED (Explain security policy violation)                                       |
+--------------------------------------------------------------------------------------------------+
                                                  │
                                 (If GENUINELY_MISSING / CONFIG_GAP)
                                                  ▼
+--------------------------------------------------------------------------------------------------+
|                                INTEGRATION DISCOVERY ENGINE                                      |
|  - Tiered Sourcing:                                                                              |
|    Tier 1: Existing Tools & Skills (re-examine registry)                                         |
|    Tier 2: Supported Integrations (built-in provider adapters)                                   |
|    Tier 3: Configured Local Integrations (local manifests, user-configured paths)                |
|    Tier 4: Trusted Package Catalogs (curated Python/tool definitions)                            |
|    Tier 5: External Candidates (tracked with full provenance, dependencies, license)             |
|  - Produces CandidateExtension with untrusted metadata flagging                                 |
+--------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+--------------------------------------------------------------------------------------------------+
|                                  EXTENSION PROPOSAL MODEL                                        |
|  - Unique proposal_id, target_goal, candidate_extension, rationale, rollback_plan                |
|  - State Machine:                                                                                |
|    DETECTED -> INVESTIGATING -> PROPOSED -> VALIDATING -> AWAITING_APPROVAL                      |
|    -> APPROVED / REJECTED -> INSTALLING -> VERIFYING -> ACTIVE / FAILED                           |
+--------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+--------------------------------------------------------------------------------------------------+
|                                  SAFE EXTENSION VALIDATOR                                        |
|  - Phase A: Provenance & Metadata Sanity Checks                                                  |
|  - Phase B: Static AST Inspection (checks forbidden imports: os.system, subprocess without check)|
|  - Phase C: Dependency & OS Compatibility Inspection                                             |
|  - Phase D: Synthetic Execution in Isolated Sandbox (synthetic data only, no real credentials)   |
|  - Output: ValidationReport (score, risks, clean_cleanup)                                        |
+--------------------------------------------------------------------------------------------------+
                                                  │
                                                  ▼
+--------------------------------------------------------------------------------------------------+
|                             APPROVAL & ACTIVATION GATEWAY                                        |
|  - Scope check: explicit approval for proposal_id, hash-pinned against candidate code            |
|  - Material change detection: if version, code, or permissions change, forces REAPPROVAL         |
|  - Activation: registers capability into CapabilityRegistry in dormant or active state           |
|  - Audit: emits sanitized events to EventBus, records audit entry in SqliteMemoryStore           |
+--------------------------------------------------------------------------------------------------+
```

---

## 5. Security & Resource Constraints (Dual-Core Windows 10)

1. **Zero Silent Privilege Escalation:** Discovery, proposal generation, and validation do not grant runtime permissions.
2. **Resource Throttling:** 
   - Subprocess validation time bounded to max 10.0 seconds.
   - Memory inspection bounded to 50MB.
   - Concurrency bounded to 1 validation task at a time.
3. **No Antivirus or Security Bypass:** Never disable Windows Defender, disable firewalls, or elevate UAC.
4. **Synthetic Data Sandbox:** Validation runs in isolated temporary sandboxes with synthetic inputs; production databases, user documents, and credentials are strictly excluded.
5. **Automatic Cleanup:** Temporary files, scratch scripts, and virtual sandbox environments are cleaned up via context managers and exception handlers.

---

## 6. Implementation Components & Deliverables

1. `capability_gap.py`: `GapType`, `CapabilityGap`, `GapAnalysisResult`, `CapabilityGapAnalyzer`.
2. `integration_discovery.py`: `CandidateSourceType`, `CandidateExtension`, `IntegrationDiscoveryEngine`.
3. `extension_proposal.py`: `ProposalStatus`, `ExtensionProposal`, `ProposalLifecycleManager`.
4. `extension_validator.py`: `ValidationStatus`, `ValidationReport`, `ExtensionValidator`.
5. `extension_manager.py`: `ExtensionManager` unifying gap analysis, discovery, proposal management, validation, and governed activation.
6. `test_phase23.py`: Comprehensive test suite verifying all 17 requirements.
