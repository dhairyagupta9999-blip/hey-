# Phase 19: Reliability, Recovery & Undo Security Architecture

## 1. Threat Model & Security Principles

Automated error recovery and undo capabilities introduce powerful mutation vectors. If not strictly governed, recovery engines can be exploited to:
1. Re-execute unauthorized commands under the guise of "retries".
2. Tamper with checkpoints or action records to cover traces of unauthorized actions.
3. Trigger destructive "compensations" or rollbacks that delete legitimate user files.
4. Exploit external agent suggestions to elevate permissions during failure handling.

To prevent these attack vectors, Hey Jev Phase 19 enforces the following zero-trust invariants:

---

## 2. Recovery Security Invariants

### Invariant 1: External Recommendations Are Data, Not Policy
When an action fails and an external model or A2A worker proposes a recovery plan (e.g. `"Run this shell command to fix the permission issue"`), the suggestion is treated as **untrusted text data**.
- External workers cannot assign themselves or Jev new permission scopes.
- Recovery plans that require elevated permissions (e.g., `EXECUTE_COMMAND`, `CHANGE_SETTINGS`) must pass human approval or fail closed.

### Invariant 2: No Rollback of Irreversible Actions
Jev categorizes every operation into explicit reversibility tiers:
- `REVERSIBLE`: Can be completely and safely undone (e.g., file move, directory rename).
- `PARTIALLY_REVERSIBLE`: State can be mitigated but not fully restored (e.g., writing lines to a shared config file).
- `IRREVERSIBLE`: Cannot be undone (e.g., sending an email, issuing a network purchase, running destructive wipe commands).
The `UndoManager` and `RecoveryDecisionEngine` strictly reject attempts to "undo" or "compensate" actions classified as `IRREVERSIBLE`.

### Invariant 3: Separation of Internal Jev Metadata vs. User Data
The `RepairManager` can automatically reconcile, rebuild, and repair Jev's **internal state** (SQLite metadata, broken index references, orphaned transaction records).
However, **User Data** (source files, downloads, repositories, user settings) is strictly protected: Jev will never silently alter or delete user files under the guise of an "automatic metadata repair".

### Invariant 4: Checkpoint & History Integrity
- **Checkpoint Tampering Defense**: Every `Checkpoint` record contains a SHA-256 checksum over its state representation. Corrupted or altered checkpoints are rejected immediately on startup.
- **Tamper-Evident Hash Chaining**: Action records in the `OperationTimeline` form a cryptographic hash chain:
  $$H_n = \text{SHA256}(H_{n-1} \,\|\, \text{action\_id} \,\|\, \text{status} \,\|\, \text{timestamp})$$
  Modifications or deletions of prior execution history are detected immediately during integrity scans.

---

## 3. Idempotency & Replay Attack Defense

1. **Deterministic Idempotency Keys**:
   - Every mutating step generates an `idempotency_key = sha256(task_id + step_id + attempt_id)`.
   - If a crash occurs after the action completed on disk but before SQLite recorded it, the `ReconciliationEngine` checks the physical environment. If the destination state is already satisfied, the step is marked `ALREADY_APPLIED`, preventing duplicate execution.
2. **Approval Replay Protection**:
   - Human approvals for high-risk operations bind strictly to: `hash(action_id + target + parameters + task_id)`.
   - Approvals expire on timeout and cannot be reused across subsequent retries or modified action parameters.
3. **Semantic Resource Locking**:
   - `SemanticLockManager` locks critical targets (e.g., `file_path`, `project_id`, `browser_session`) during execution to prevent conflicting concurrent modifications by parallel workers.
