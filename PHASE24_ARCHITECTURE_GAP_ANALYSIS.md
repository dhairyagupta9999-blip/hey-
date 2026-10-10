# Hey Jev Phase 24: Architecture & Gap Analysis
## Controlled Extension Construction, Testing and Staged Activation

---

### 1. Baseline & Context

- **Current Branch:** `windows-port`
- **Current Base Commit:** `1f6e69b` (Phase 23)
- **Phase 23 Capabilities Available:**
  - `CapabilityGapAnalyzer`: Rigorous gap taxonomy distinguishing genuine missing functions from `PERMISSION_DENIED`.
  - `IntegrationDiscoveryEngine`: Prioritized 5-tier candidate discovery.
  - `ExtensionProposal` & `ProposalLifecycleManager`: SHA-256 fingerprinting, transition validation, mutation invalidation.
  - `ExtensionValidator`: Multi-stage static AST analysis, platform verification, dependency sanity, isolated synthetic sandbox.
  - `ExtensionManager`: Coordinated lifecycle management, duplicate prevention, secret redaction, and capability activation.
  - `CapabilityRegistry`: Live tool and skill directory with outcome routing and permission enforcement.
  - `WorkflowEngine`: DAG execution with verification gates and rollback reporting.

---

### 2. The Core Problem in Phase 24

In Phase 23, Jev can discover and propose prospective extensions and validate external specs. However:
1. **Proposal-to-Code Gap:** Jev cannot currently synthesize a verifiable, concrete implementation from an approved proposal.
2. **Execution Boundary Distinction:** A temporary directory is a file-system boundary, NOT an OS process security boundary. Running candidate code directly in the main Jev Python runtime risks corrupting memory or hijacking execution.
3. **Dependency Blindness:** Dependency installation must never be silent, global, or uninspected. Dependency inspection must be decoupled from dependency installation.
4. **Staged Execution & Rollback:** Before committing an extension into production `CapabilityRegistry`, it must run through staged sandbox execution where contract adherence, permission containment, timeouts, and rollback reversibility are strictly tested.
5. **Human Review Fidelity:** Jev must present a comprehensive review package (diff, permissions, file manifest, executed tests, skipped/blocked tests, and remaining risks) rather than opaque "success" indicators.

---

### 3. Architecture Design & Component Boundaries

```
[Approved ExtensionProposal (Phase 23)]
                 │
                 ▼
     [ExtensionBuildPlan]
   - Functional requirements & contracts
   - Whitelisted relative paths
   - Bounded resources & timeouts
   - Permitted build commands
                 │
                 ▼
    [ExtensionWorkspace]
   - Canonical root containment
   - Strict path traversal defense
   - Zero credential exposure
   - Concurrency locking & cleanups
                 │
                 ▼
      [ExtensionBuilder]
   - Contract-enforced Python code gen
   - Clear capability & handler interface
   - Error & timeout handlers
   - No eval/exec, no policy overrides
                 │
                 ▼
   [DependencyInspector]
   - Static provenance & license check
   - Existing dependency preference
   - Explicit InstallationPlan
   - No silent global package installs
                 │
                 ▼
  [StagedValidationPipeline] (11 Stages)
   - Outcomes: PASS, FAIL, BLOCKED, NOT_RUN, UNVERIFIED
   - Synthetic unit tests & isolated smoke tests
   - ReviewPackage with manifest & diff
                 │
                 ▼
      [READY_FOR_REVIEW]
                 │
            (Human Approval)
                 │
                 ▼
   [StagedExtensionRuntime]
   - Sandboxed staging execution
   - Contract & permission verification
   - Rollback & unregister verification
                 │
                 ▼
       [Production Activation]
```

---

### 4. Detailed Component Specifications

#### 4.1 `ExtensionBuildPlan` (`extension_build_plan.py`)
- Associated with an existing proposal and immutable candidate content hash (`candidate_hash`).
- Whitelists permitted files (relative paths inside workspace only).
- Defines functional requirements, input/output schema, and target permissions.
- Specifies timeout (default 5.0s, max 30.0s), memory bounds, and artifact cleanup rules.
- Rejects incomplete, rejected, expired, or materially modified proposals.
- Invariant: A build plan authorizes construction, NEVER execution or installation.

#### 4.2 `ExtensionWorkspace` (`extension_workspace.py`)
- Creates dedicated directory under `%LOCALAPPDATA%/hey-jev/build_workspaces/<workspace_id>`.
- Canonicalizes all paths with `os.path.realpath()`.
- Rejects path traversal (`..`, drive letters, absolute outside paths).
- Restricts writes strictly to authorized files declared in build plan.
- Keeps credentials, production configuration (`config.json`), and tokens completely out of workspace.
- Tracks generated file list, sizes, and SHA-256 hashes.
- Concurrency lock per workspace ID.
- Reliable cleanup via context manager or explicit `cleanup()` with fallback to force cleanup.

#### 4.3 `ExtensionBuilder` (`extension_builder.py`)
- Generates structured, reviewable Python modules implementing `CapabilityDefinition` and `ToolDefinition`.
- Each implementation includes:
  - Unique capability ID
  - Explicit typed input arguments and output schema
  - Declared permissions matching proposal
  - Try/except safety wrapper with bounded timeouts
  - Sensitive data scrubbing on inputs and outputs
  - Synthetic test fixtures embedded for validation
- Defends against untrusted instructions: static scan rejects dangerous AST patterns (`eval`, `exec`, `__import__`, `os.system`, `ctypes`, `subprocess.Popen`).

#### 4.4 `DependencyInspector` (`dependency_inspector.py`)
- Analyzes candidate dependency requirements against Python's standard library and current virtual environment.
- Assesses security risks, licensing compatibility (MIT, Apache-2.0, BSD preferred), and publisher provenance.
- Generates `InstallationPlan` explaining network access, external binaries, and reasons why existing dependencies cannot suffice.
- Invariant: Zero silent installation. If isolated virtual environment is unavailable, flags dependency checks as `BLOCKED_ISOLATION` or `NOT_RUN`.

#### 4.5 `StagedValidationPipeline` (`staged_validation.py`)
- Executes 11 staged validation gates:
  1. Validate proposal state and candidate identity hash.
  2. Create and verify isolated workspace containment.
  3. Validate file manifest against permitted paths.
  4. Inspect source code and dependency metadata.
  5. Run static AST safety scans.
  6. Run unit tests with synthetic data.
  7. Run integration smoke tests in staging sandbox.
  8. Enforce execution time limits and resource bounds.
  9. Validate error handling on malformed inputs.
  10. Generate reviewable file diff and cryptographic manifest.
  11. Evaluate readiness: mark `READY_FOR_REVIEW` only if required gates are `PASS`.
- Strict Stage Outcomes: `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`, `UNVERIFIED`.
- Invariant: Skipped checks are never disguised as passes. Static analysis is never claimed as proof of execution safety.

#### 4.6 `StagedExtensionRuntime` (`staged_runtime.py`)
- Provides restricted staging environment for approved candidates before production activation.
- Exercises capability handler using synthetic test payload.
- Verifies:
  - Only approved capability ID is registered.
  - Declared permissions equal effective permissions.
  - Input/output contracts hold.
  - Timeouts and cancellation are respected.
  - Rollback cleanly unregisters capability and removes staged files without leaving orphan artifacts.

#### 4.7 Lifecycle State Model Integration (`extension_proposal.py`)
Extends lifecycle tracking with build and staging states:
- `PROPOSED` $\rightarrow$ `BEING_BUILT` $\rightarrow$ `BUILT_UNVALIDATED`
- $\rightarrow$ `VALIDATING` $\rightarrow$ `VALIDATION_FAILED` / `BLOCKED_ISOLATION`
- $\rightarrow$ `READY_FOR_REVIEW` $\rightarrow$ `AWAITING_APPROVAL`
- $\rightarrow$ `APPROVED` $\rightarrow$ `STAGED` $\rightarrow$ `ACTIVE`
- Terminal states: `REJECTED`, `DEACTIVATED`, `FAILED_ABANDONED`.

---

### 5. Verification Plan

1. **Dedicated Phase 24 Test Suite (`test_phase24.py`):**
   - 15 specific test scenarios covering build plan rejection, workspace traversal prevention, static analysis without execution, dependency inspection separation, 11-stage pipeline outcomes, review package presentation, staged runtime verification, rollback cleanup, and secret redaction.
2. **Full Regression Suite (`pytest -q`):**
   - Baseline 638 tests + Phase 24 tests must all pass with zero failures.
3. **Preflight Truth Suite (`preflight.py`):**
   - Verify 13 pass, 0 fail, 1 warning (offline MCP service).
4. **Git State:**
   - Commit cleanly on `windows-port` without modifying auth or config files.
