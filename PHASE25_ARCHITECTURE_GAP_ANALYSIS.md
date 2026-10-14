# Hey Jev Phase 25: Architecture & Gap Analysis
## Capability Deployment, Versioning, Activation and Lifecycle Recovery

---

### 1. Baseline & Context

- **Current Branch:** `windows-port`
- **Current Base Commit:** `cd7ce97` (Phase 24.1 Security Audit)
- **Phase 24.1 Hardened Invariants:**
  - NTFS Alternate Data Streams (`:`) and Windows DOS reserved device names (`NUL`, `CON`, `PRN`, `AUX`, `COM1..9`, `LPT1..9`) are strictly blocked in workspaces.
  - Static AST inspection is recognized as a heuristic filter, NOT an OS process security boundary.
  - OS-level process isolation is unavailable on the host Windows desktop environment without external container runtimes.
  - Generated candidate Python files in workspaces remain inert on disk. Untrusted candidate code is NEVER executed inside the live Jev process.
  - System operates in a **fail-closed posture**: untrusted executable candidates must remain inactive rather than disguised through synthetic handlers.

---

### 2. The Core Challenge in Phase 25

Phase 24 introduced controlled construction, workspace containment, and staged validation. However, significant architectural gaps remain before extensions can be safely managed across their operational lifetime:

1. **Manifest Identity & Immutability Gap:**
   Previously, activation referenced a proposal ID and candidate slug. A mutable directory, version string, or candidate name alone is NOT proof of deployment identity. Phase 25 requires an immutable `DeploymentManifest` bound cryptographically (SHA-256) to exact code, dependencies, input/output contracts, permissions, validation evidence, and approval grants.

2. **Execution Boundary & Deployment Tier Classification:**
   The system must explicitly categorize capabilities into four architectural deployment tiers:
   - **Tier 1: Existing Trusted Capability:** Built-in tool or skill registered via standard tool/skill registries.
   - **Tier 2: Approved External Integration:** Tested adapter (e.g. SQLite explorer, PDF tools, CSV reader) with verified provenance and supported execution adapter.
   - **Tier 3: Declarative Extension:** Non-executable manifest or restricted workflow description interpreted by Jev's trusted workflow engine with strict input/action validation.
   - **Tier 4: Executable Code:** Generated or third-party executable code requiring OS process isolation. Under Phase 24.1's fail-closed rule, this tier MUST remain inactive in `BLOCKED_ISOLATION` when OS process isolation is unavailable, with clear explanation of the blocker.

3. **Registry Mutation & Collision Safety:**
   Activating an extension directly modified the production `CapabilityRegistry` without pre-checking for naming collisions with unrelated tools, without snapshotting the previous registry state, and without guaranteeing atomic rollback if post-activation health checks failed.

4. **In-Flight Protection & Controlled Deactivation:**
   Previously, unregistering a capability removed it immediately from the dictionary without draining or protecting in-flight invocations. Deactivation must transition through `DEACTIVATING` to reject new callers while safely completing or timing out active calls.

5. **Observable Health Checks vs Model Speculation:**
   Previously, health was assumed based on registration alone. Phase 25 requires bounded, observable health checks verifying executor availability, dependency presence, response contract adherence, and resource constraints without side effects.

6. **Crash Recovery & Idempotency:**
   If the Jev runtime crashes or restarts during activation, deactivation, or rollback, the previous system had no mechanism to inspect the physical registry state and safely recover. Phase 25 requires recovery on restart that re-verifies approvals, candidate hashes, and executor health before determining active state.

---

### 3. Architecture Design & Component Boundaries

```
[Approved Proposal / Candidate]
                │
                ▼
      [DeploymentManifest] ─────────────── Immutable SHA-256 Content Hash
   (Tiers: Existing, Adapter,             (Changes invalidate approval)
    Declarative, Executable)
                │
                ▼
[CapabilityDeploymentManager]
   ├── 1. CANDIDATE
   ├── 2. STAGING ─────────────────────── Staged runtime sandbox (zero prod changes)
   ├── 3. STAGED
   ├── 4. READY_FOR_APPROVAL
   ├── 5. APPROVED ────────────────────── Human approval bound to manifest hash
   │
   ▼
[2-Phase Transactional Deployment]
   ├── Phase A: Prepare
   │     - Collision check against unrelated tools
   │     - Dependency & executor availability check
   │     - Tier check (Executable Code -> BLOCKED_ISOLATION if unisolated)
   │     - Snapshot existing registration (for clean rollback)
   │
   ├── Phase B: Apply
   │     - Register in live CapabilityRegistry
   │     - Status -> ACTIVATING
   │
   ├── Phase C: Verify Health
   │     - Observable bounded health check (< 5.0s)
   │     - On Pass -> ACTIVE
   │     - On Fail -> Trigger ROLLBACK_PENDING -> ROLLED_BACK
   │
   ▼
[Controlled Deactivation]
   ├── 1. Move to DEACTIVATING (reject new callers immediately)
   ├── 2. Drain in-flight invocations (active call counter)
   ├── 3. Remove registration from CapabilityRegistry
   └── 4. Verify post-condition (registry.get(id) is None) -> INACTIVE
```

---

### 4. Component Reuse & Extension Plan

1. **`deployment_manifest.py` (New):**
   - Defines `DeploymentType` (Enum: `EXISTING_TRUSTED`, `APPROVED_EXTERNAL`, `DECLARATIVE_EXTENSION`, `EXECUTABLE_CODE`).
   - Defines `DeploymentStatus` (Enum: 17 states matching the full lifecycle and recovery paths).
   - Defines `DeploymentManifest` (Dataclass with deterministic SHA-256 fingerprinting, contracts, health check spec, and rollback plans).
   - Defines `VALID_DEPLOYMENT_TRANSITIONS` (State transition matrix).

2. **`capability_registry.py` (Extended):**
   - Add snapshot and transactional rollback support:
     - `backup_capability(capability_id)` -> returns copy of `CapabilityDefinition` if present.
     - `restore_capability(backup)` -> restores previous snapshot.
     - `is_registered(capability_id)` -> explicit registration check.

3. **`capability_deployment.py` (New):**
   - `CapabilityDeploymentManager`:
     - Lifecycle coordinator integrating `CapabilityRegistry`, `EventBus`, and persistent memory.
     - Enforces Tier 4 fail-closed isolation check (`has_process_isolation=False` -> `BLOCKED_ISOLATION`).
     - In-flight call tracking (`active_invocations`) and invocation locking.
     - Two-phase prepare-apply-verify deployment with automatic rollback on health failure.
     - Bounded health checking (< 5.0s timeout, resource governor awareness).
     - Crash recovery on startup (`recover_deployments()`).
     - Persistent audit history with secret scrubbing (`sanitize_content`).

4. **`extension_manager.py` (Updated):**
   - Integrate `CapabilityDeploymentManager` with existing `ExtensionManager`.
   - Provide seamless deployment methods (`deploy_approved_candidate`, `deactivate_deployed_capability`, `rollback_deployed_capability`).

5. **`runtime_manager.py` (Updated):**
   - Wire `deployment_manager` into `JevRuntime` and invoke `recover_deployments()` upon startup.

---

### 5. Invariants & Security Boundaries

1. **Fail-Closed on Unisolated Executable Code:**
   If `deployment_type == EXECUTABLE_CODE` and `has_process_isolation == False`, the deployment MUST be set to `BLOCKED_ISOLATION`. Jev will NEVER register a synthetic dummy and claim that untrusted generated code was activated.
2. **Deterministic Manifest Hashing:**
   The manifest content hash is computed over all functional attributes (ID, version, code hash, dependencies, permissions, input/output schemas, deployment type). Any mutation invalidates previous approvals.
3. **No Unrelated Tool Overwrites:**
   Deploying capability `X` cannot overwrite an existing registered capability `X` unless it shares the same proposal/provenance (version update).
4. **Clean In-Flight Drainage:**
   Deactivation strictly moves to `DEACTIVATING` and prevents new calls from entering while allowing current calls to cleanly finish.
5. **Idempotent and Recoverable Operations:**
   Repeated activation or deactivation calls return deterministic outcomes without side effects. Crashes during intermediate states (`ACTIVATING`, `DEACTIVATING`, `ROLLBACK_PENDING`) are detected and resolved upon startup.
