# Phase 15 Security Architecture & Zero-Trust Governance

## Executive Summary

Phase 15 transforms Hey Jev from a system with hardcoded procedural actions into an extensible, dynamic architecture governed by the strict execution hierarchy:
$$\text{Mission} \longrightarrow \text{Task} \longrightarrow \text{Agent} \longrightarrow \text{Skill} \longrightarrow \text{Tool} \longrightarrow \text{System}$$

Because skills and tools can be registered dynamically, loaded from internal modules, and composed into multi-step workflows, security cannot rely on caller honesty or agent intentions. Phase 15 introduces a **Zero-Trust Tool & Skill Execution Boundary** that operates with kernel-style least privilege and non-bypassable gating.

---

## 1. Zero-Trust Permission Boundary

### 1.1 Non-Bypassable Enforcement at the Executor
- **The Core Rule**: An agent claiming "I am executing this tool" does *not* grant authority.
- The `ToolExecutor` and `SkillExecutor` serve as non-bypassable policy enforcement points.
- Before any tool or skill handler is invoked, the executor:
  1. Validates the caller's explicit capability token (`granted_permissions`).
  2. Compares against the declared `TaskPermission` requirements in the `ToolDefinition` or `SkillDefinition`.
  3. Rejects with `BLOCKED` status if even a single required permission is missing (`Missing required permission: '<perm>'`).
  4. Prevents ambient authority: callers never inherit permissions from parent agents or higher-privilege processes.

### 1.2 Risk Classification Tiers & Human Approval Gating

Every tool and skill is strictly assigned to one of four `RiskLevel` tiers:

| Risk Level | Scopes & Operations | Authorization Model | Default Gating |
| :--- | :--- | :--- | :--- |
| **`LOW`** | Directory listing, screen inspection, active window inspection, search | Automatic execution if permissions granted | No human approval required |
| **`MEDIUM`** | File moving, wallpaper change, desktop setting changes, form entry | Automatic execution with rollback capture | Requires caller `WRITE_FILES` or `CHANGE_SETTINGS` |
| **`HIGH`** | File deletion, system command execution, account manipulation | **Explicit Human Approval Required** | Must receive `approval_state == "APPROVED"` |
| **`CRITICAL`** | Credential manipulation, payments, security policy re-configuration | **Explicit Interactive MFA / Confirmation** | Hard-blocked without elevated interactive confirmation |

Any invocation of a `HIGH` or `CRITICAL` tool without an explicit approval state returns `status="BLOCKED"` with `error="High-risk operation requires explicit human approval"`.

---

## 2. Skill Trust Tiers & Boundary Isolation

Skills are classified into five explicit trust classifications:

1. **`CORE`**: Built-in native capabilities shipped with Hey Jev core. Validated and statically audited.
2. **`VERIFIED`**: Internal extensions validated through end-to-end integration test suites.
3. **`COMMUNITY`**: Packaged third-party skills adhering to strict JSON/YAML manifest schemas with declared licensing.
4. **`EXPERIMENTAL`**: Sandboxed workflows under active development; restricted to non-destructive actions.
5. **`UNTRUSTED`**: External or user-imported definitions without proven integrity.

### 2.1 Untrusted Skill Invariants
- `UNTRUSTED` skills are strictly barred from executing operations exceeding `RiskLevel.LOW`.
- Even if human approval is supplied, an `UNTRUSTED` skill cannot invoke file modifications, command execution, or setting alterations.
- An attempt by an `UNTRUSTED` skill to run `MEDIUM`, `HIGH`, or `CRITICAL` tools immediately fails with `AgentResultStatus.BLOCKED`.

---

## 3. Strict Parameter Sanitization & Prompt Injection Quarantine

### 3.1 Input Schema Type & Constraint Checking
- Every `ToolDefinition` mandates a machine-readable JSON schema for `input_schema`.
- `ToolExecutor.validate_input` enforces:
  - Required parameter presence.
  - Strict type checking (`string`, `integer`, `boolean`, `array`, `object`).
  - Rejection of unexpected scalar coercion (e.g. passing arbitrary string commands to numeric inputs).

### 3.2 Path Traversal & Injection Quarantine
- Filesystem parameters (`source`, `destination`, `path`) are normalized through canonical absolute path resolution.
- External untrusted inputs (e.g., web search results, extracted DOM text) are treated as data, never interpreted as commands.
- Context parameter substitution in workflows uses explicit `$key` variable referencing; unreferenced variables are discarded.

---

## 4. Open Source Governance & License Protection

Every tool and skill specification includes an explicit `license` field (e.g. `MIT`, `Apache-2.0`).
- **GPL Contamination Guard**: `SkillRegistry.validate` and `ToolRegistry` verify that third-party skills declare approved permissive licenses.
- Code from reciprocal or copyleft licenses (e.g., AGPL) cannot be loaded dynamically without administrative declaration.
- Rejection of malformed, unlicensed, or corrupted manifests during registration prevents untracked intellectual property ingestion.

---

## 5. Self-Modification & Immutability Boundaries

To prevent prompt hijacking or recursive agent drift:
1. **Immutable Engine Core**: Skills and tools do not possess write permissions to Hey Jev core engine files (`agent_*.py`, `tool_*.py`, `skill_*.py`, `orchestrator.py`).
2. **Registry Mutex Protection**: Tool and skill registries use re-entrant thread locks (`RLock`) to prevent race conditions or simultaneous mutation during concurrent agent workflows.
3. **Rollback & Provenance**: Every state mutation produces structured `rollback_metadata` (`undo_action`, `restore_source`, `restore_destination`, `before_state`), enabling instantaneous reversion of unintended changes.
4. **Zero Key Exposure**: In accordance with the Hey Jev Security Invariant, API tokens, passwords, and private identifiers are never written to `ToolResult.output`, logs, or event broadcasts.
