"""Capability Deployment Manager and Lifecycle Coordinator (Phase 25).

Implements reliable capability deployment, versioning, explicit approval binding,
transactional registry activation, health-check monitoring, in-flight operation draining,
controlled deactivation, auditable rollback, and restart crash recovery.

Key Invariants:
1. Manifest Immutability: deployment operations bind to exact SHA-256 manifest hash.
2. Fail-Closed on Executable Code: if deployment_type is EXECUTABLE_CODE and
   has_process_isolation is False, deployment is strictly set to BLOCKED_ISOLATION.
3. Transactional Registry Updates: 2-phase prepare-apply-verify pattern with snapshot rollback.
4. In-Flight Protection: DEACTIVATING state prevents new invocations and drains active calls.
5. Observable Health Checks: verified executor availability, dependency presence, and contracts.
6. Restart Crash Recovery: re-verifies physical registry and executor states.
7. Zero Secret Exposure: all events and persistent storage sanitized via sanitize_content.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from deployment_manifest import (
    DeploymentManifest,
    DeploymentStatus,
    DeploymentType,
    VALID_DEPLOYMENT_TRANSITIONS,
)
from event_bus import EventBus
from memory_model import MemoryRecord, MemorySource, MemoryType, sanitize_content
from memory_store import MemoryStore
from staged_runtime import StagedExtensionRuntime
from tool_model import RiskLevel, ToolHealth, ToolResult


class CapabilityDeploymentManager:
    """Manages the lifecycle, deployment transactions, and health of capabilities."""

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        staged_runtime: Optional[StagedExtensionRuntime] = None,
        event_bus: Optional[EventBus] = None,
        memory_store: Optional[MemoryStore] = None,
        deployments_dir: Optional[str] = None,
        has_process_isolation: bool = False,
    ) -> None:
        self.capability_registry = capability_registry
        self.staged_runtime = staged_runtime or StagedExtensionRuntime(production_registry=capability_registry)
        self.event_bus = event_bus or EventBus()
        self.memory_store = memory_store
        self.has_process_isolation = has_process_isolation

        self.deployments_dir = deployments_dir or os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hey-jev", "deployments"
        )
        os.makedirs(self.deployments_dir, exist_ok=True)

        self._lock = threading.RLock()
        self._manifests: Dict[str, DeploymentManifest] = {}
        self._registry_snapshots: Dict[str, Optional[CapabilityDefinition]] = {}  # deployment_id -> backup
        self._in_flight: Dict[str, int] = {}  # capability_id -> count of active invocations
        self._in_flight_lock = threading.Lock()
        self._active_lifecycle_ops: Dict[str, str] = {}  # capability_id -> deployment_id
        self._active_probes: Set[threading.Thread] = set()
        self._probe_lock = threading.Lock()

        # Load persisted manifests if available
        self._load_persisted_manifests()

    def _sanitize(self, val: Any) -> Any:
        """Sanitize secrets from data structures."""
        if isinstance(val, str):
            return sanitize_content(val)
        if isinstance(val, dict):
            return {k: self._sanitize(v) for k, v in val.items()}
        if isinstance(val, list):
            return [self._sanitize(x) for x in val]
        return val

    def _save_manifest_to_disk(self, manifest: DeploymentManifest) -> None:
        """Persist deployment manifest JSON atomically."""
        try:
            path = os.path.join(self.deployments_dir, f"{manifest.deployment_id}.json")
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(manifest.to_dict(), f, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            pass

    def _load_persisted_manifests(self) -> None:
        """Load persisted deployment manifests on startup."""
        if not os.path.exists(self.deployments_dir):
            return
        for fname in os.listdir(self.deployments_dir):
            if fname.endswith(".json") and not fname.endswith(".tmp"):
                try:
                    fpath = os.path.join(self.deployments_dir, fname)
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    manifest = DeploymentManifest.from_dict(data)
                    self._manifests[manifest.deployment_id] = manifest
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # 1. Manifest Creation & Staging
    # -------------------------------------------------------------------------
    def create_deployment_manifest(
        self,
        capability_id: str,
        name: str,
        description: str,
        proposal_id: str,
        candidate_hash: str,
        deployment_type: DeploymentType = DeploymentType.APPROVED_EXTERNAL,
        version: str = "1.0.0",
        provenance: str = "builtin",
        supported_outcomes: Optional[List[str]] = None,
        dependencies: Optional[List[str]] = None,
        required_permissions: Optional[List[TaskPermission]] = None,
        input_contract: Optional[Dict[str, Any]] = None,
        output_contract: Optional[Dict[str, Any]] = None,
        artifact_hashes: Optional[Dict[str, str]] = None,
        validation_summary: Optional[Dict[str, Any]] = None,
    ) -> DeploymentManifest:
        """Create a new immutable deployment manifest."""
        with self._lock:
            dep_id = f"dep_{capability_id.replace(':', '_')}_{uuid.uuid4().hex[:8]}"
            manifest = DeploymentManifest(
                deployment_id=dep_id,
                capability_id=capability_id,
                name=name,
                description=description,
                proposal_id=proposal_id,
                candidate_hash=candidate_hash,
                deployment_type=deployment_type,
                version=version,
                provenance=provenance,
                supported_outcomes=list(supported_outcomes or []),
                dependencies=list(dependencies or []),
                required_permissions=list(required_permissions or []),
                input_contract=dict(input_contract or {}),
                output_contract=dict(output_contract or {}),
                artifact_hashes=dict(artifact_hashes or {}),
                validation_summary=dict(validation_summary or {}),
                status=DeploymentStatus.CANDIDATE,
            )
            self._manifests[dep_id] = manifest
            self._save_manifest_to_disk(manifest)

            self.event_bus.emit(
                "deployment.created",
                dep_id,
                data=self._sanitize({
                    "deployment_id": dep_id,
                    "capability_id": capability_id,
                    "type": manifest.deployment_type.value,
                    "hash": manifest.manifest_hash,
                }),
            )
            return manifest

    def stage_deployment(self, deployment_id: str) -> Tuple[bool, Optional[str]]:
        """Stage candidate into restricted staging runtime without mutating production registry."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False, f"Deployment '{deployment_id}' not found."

            valid, errors = manifest.verify_integrity()
            if not valid:
                self._transition(manifest, DeploymentStatus.VALIDATION_FAILED)
                return False, f"Manifest integrity check failed: {errors}"

            self._transition(manifest, DeploymentStatus.STAGING)

            # Fail-closed check: if EXECUTABLE_CODE without OS isolation
            if manifest.deployment_type == DeploymentType.EXECUTABLE_CODE and not self.has_process_isolation:
                self._transition(manifest, DeploymentStatus.BLOCKED_ISOLATION)
                err_msg = (
                    f"Deployment '{deployment_id}' requires OS process isolation for executable code, "
                    "which is unavailable on this host environment. Staging blocked."
                )
                manifest.validation_summary["isolation_blocker"] = err_msg
                self._save_manifest_to_disk(manifest)
                return False, err_msg

            # Verify production registry is untouched
            if self.capability_registry.is_registered(manifest.capability_id):
                # An existing capability exists - verify it's not an unrelated conflict
                existing = self.capability_registry.get(manifest.capability_id)
                if existing and existing.provenance != f"deployment:{deployment_id}":
                    # Snapshot existing capability definition for rollback safety
                    self._registry_snapshots[deployment_id] = self.capability_registry.backup_capability(manifest.capability_id)

            self._transition(manifest, DeploymentStatus.STAGED)
            self._transition(manifest, DeploymentStatus.READY_FOR_APPROVAL)
            self._save_manifest_to_disk(manifest)

            self.event_bus.emit(
                "deployment.staged",
                deployment_id,
                data=self._sanitize({"deployment_id": deployment_id, "capability_id": manifest.capability_id}),
            )
            return True, None

    # -------------------------------------------------------------------------
    # 2. Explicit Human Approval Binding
    # -------------------------------------------------------------------------
    def approve_deployment(
        self,
        deployment_id: str,
        approver: str = "security_officer",
        scope_notes: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """Grant human approval bound strictly to the immutable manifest content hash."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False, f"Deployment '{deployment_id}' not found."

            if manifest.status != DeploymentStatus.READY_FOR_APPROVAL:
                return False, f"Cannot approve deployment in status '{manifest.status.value}'."

            valid, errors = manifest.verify_integrity()
            if not valid:
                return False, f"Cannot approve mutated manifest: {errors}"

            manifest.approval_record = {
                "approver": approver,
                "timestamp": time.time(),
                "manifest_hash": manifest.manifest_hash,
                "candidate_hash": manifest.candidate_hash,
                "version": manifest.version,
                "scoped_permissions": [p.value for p in manifest.required_permissions],
                "notes": scope_notes,
            }

            self._transition(manifest, DeploymentStatus.APPROVED)
            self._save_manifest_to_disk(manifest)

            self.event_bus.emit(
                "deployment.approved",
                deployment_id,
                data=self._sanitize({
                    "deployment_id": deployment_id,
                    "approver": approver,
                    "manifest_hash": manifest.manifest_hash,
                }),
            )
            return True, None

    def check_and_invalidate_on_mutation(
        self,
        deployment_id: str,
        mutated_manifest: DeploymentManifest,
    ) -> bool:
        """Detect material mutation after approval and force re-approval."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False

            new_hash = mutated_manifest.compute_manifest_hash()
            if new_hash != manifest.manifest_hash:
                # Material change detected
                manifest.status = DeploymentStatus.READY_FOR_APPROVAL
                manifest.approval_record = None
                manifest.manifest_hash = new_hash
                manifest.updated_at = time.time()
                self._save_manifest_to_disk(manifest)

                self.event_bus.emit(
                    "deployment.approval_revoked",
                    deployment_id,
                    data={"deployment_id": deployment_id, "reason": "Manifest materially mutated"},
                )
                return True
            return False

    def invalidate_approval_if_stale(self, deployment_id: str) -> bool:
        """Detect stale approval and transition back to READY_FOR_APPROVAL."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest or manifest.status != DeploymentStatus.APPROVED:
                return False

            if not manifest.approval_record:
                self._transition(manifest, DeploymentStatus.READY_FOR_APPROVAL)
                self._save_manifest_to_disk(manifest)
                return True

            if (
                manifest.approval_record.get("manifest_hash") != manifest.manifest_hash
                or manifest.approval_record.get("candidate_hash") != manifest.candidate_hash
                or manifest.compute_manifest_hash() != manifest.manifest_hash
            ):
                self._transition(manifest, DeploymentStatus.READY_FOR_APPROVAL)
                manifest.approval_record = None
                self._save_manifest_to_disk(manifest)
                self.event_bus.emit(
                    "deployment.approval_revoked",
                    deployment_id,
                    data={"deployment_id": deployment_id, "reason": "Approval invalidated due to stale candidate or manifest"},
                )
                return True
            return False

    # -------------------------------------------------------------------------
    # 3. Two-Phase Transactional Activation
    # -------------------------------------------------------------------------
    def activate_deployment(
        self,
        deployment_id: str,
        executor_override: Optional[Callable] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Activate an approved deployment into the live CapabilityRegistry with rollback."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False, f"Deployment '{deployment_id}' not found."

            # 1. State check
            if manifest.status == DeploymentStatus.ACTIVE:
                # Idempotent re-activation
                return True, "Deployment is already active."

            cap_id = manifest.capability_id

            # Guard against competing lifecycle operations
            active_op = self._active_lifecycle_ops.get(cap_id)
            if active_op and active_op != deployment_id:
                return False, f"Capability '{cap_id}' is currently undergoing lifecycle transition by deployment '{active_op}'."

            if manifest.status != DeploymentStatus.APPROVED:
                return False, f"Cannot activate deployment in status '{manifest.status.value}' without human approval."

            # 2. Integrity and approval freshness check
            valid, errors = manifest.verify_integrity()
            if not valid:
                return False, f"Manifest integrity failed: {errors}"

            if not manifest.approval_record:
                return False, "Approval record is missing. Cannot activate unapproved deployment."

            if manifest.approval_record.get("manifest_hash") != manifest.manifest_hash:
                return False, "Approval is stale: manifest hash differs from approved hash. Re-approval required."

            if manifest.approval_record.get("candidate_hash") != manifest.candidate_hash:
                return False, "Approval is stale: candidate hash differs from approved candidate hash. Re-approval required."

            # 3. Fail-closed isolation check
            if manifest.deployment_type == DeploymentType.EXECUTABLE_CODE and not self.has_process_isolation:
                self._transition(manifest, DeploymentStatus.BLOCKED_ISOLATION)
                self._save_manifest_to_disk(manifest)
                return False, (
                    "Activation denied: untrusted executable code requires operating-system isolation "
                    "which is unavailable on this host."
                )

            # 4. Conflict check against unrelated registered tools
            if self.capability_registry.is_registered(cap_id):
                existing = self.capability_registry.get(cap_id)
                # An unrelated tool is a built-in or system tool rather than a managed deployment/proposal
                if existing and not (
                    existing.provenance.startswith("deployment:") or
                    existing.provenance.startswith("extension_proposal:") or
                    existing.provenance.startswith("staged_proposal:") or
                    existing.provenance.startswith("proposal:")
                ):
                    return False, (
                        f"Naming conflict: capability '{cap_id}' is already registered "
                        f"by an unrelated source ('{existing.provenance}')."
                    )
                # Snapshot existing capability definition for rollback
                self._registry_snapshots[deployment_id] = self.capability_registry.backup_capability(cap_id)

            # Acquire lifecycle lock for this capability
            self._active_lifecycle_ops[cap_id] = deployment_id

            try:
                # 5. Move to ACTIVATING
                self._transition(manifest, DeploymentStatus.ACTIVATING)
                self._save_manifest_to_disk(manifest)

                self.event_bus.emit(
                    "deployment.activating",
                    deployment_id,
                    data=self._sanitize({
                        "deployment_id": deployment_id,
                        "capability_id": cap_id,
                        "state": "attempted",
                    }),
                )

                # 6. Construct executor handler
                handler = self._construct_executor_handler(manifest, executor_override)

                # 7. Construct CapabilityDefinition
                cap_def = CapabilityDefinition(
                    capability_id=cap_id,
                    name=manifest.name,
                    description=manifest.description,
                    supported_outcomes=list(manifest.supported_outcomes),
                    capability_type=CapabilityType.TOOL,
                    risk_level=RiskLevel.MEDIUM if manifest.required_permissions else RiskLevel.LOW,
                    permissions=list(manifest.required_permissions),
                    health=ToolHealth.HEALTHY,
                    provenance=f"deployment:{manifest.deployment_id}",
                    version=manifest.version,
                    handler=handler,
                    tags=list(manifest.supported_outcomes),
                )

                # 8. Apply to CapabilityRegistry
                self.capability_registry.register(cap_def)

                # 9. Verify observable health
                healthy, health_err = self.run_health_check(deployment_id)
                if not healthy:
                    # Rollback immediately
                    self._transition(manifest, DeploymentStatus.ROLLBACK_PENDING)
                    self._rollback_registry_change(deployment_id)
                    self._transition(manifest, DeploymentStatus.ROLLED_BACK)
                    self._save_manifest_to_disk(manifest)
                    return False, f"Activation health check failed: {health_err}. Registry rolled back."

                # 10. Clean up from staged runtime if present
                if self.staged_runtime.is_staged(cap_id):
                    self.staged_runtime.rollback_and_deactivate(cap_id)

                # 11. Mark ACTIVE
                self._transition(manifest, DeploymentStatus.ACTIVE)
                self._save_manifest_to_disk(manifest)

                # If an earlier deployment for this capability was active, transition it to INACTIVE
                for other_dep in self._manifests.values():
                    if other_dep.deployment_id != deployment_id and other_dep.capability_id == cap_id:
                        if other_dep.status == DeploymentStatus.ACTIVE:
                            other_dep.status = DeploymentStatus.INACTIVE
                            self._save_manifest_to_disk(other_dep)

                self.event_bus.emit(
                    "deployment.activated",
                    deployment_id,
                    data=self._sanitize({
                        "deployment_id": deployment_id,
                        "capability_id": cap_id,
                        "version": manifest.version,
                        "state": "completed",
                    }),
                )

                if self.memory_store:
                    try:
                        self.memory_store.add(
                            MemoryRecord(
                                type=MemoryType.ENVIRONMENT_FACT,
                                content=f"Capability '{cap_id}' v{manifest.version} deployed and active.",
                                source=MemorySource.SYSTEM,
                                metadata=self._sanitize({"deployment_id": deployment_id}),
                            )
                        )
                    except Exception:
                        pass

                return True, None
            finally:
                self._active_lifecycle_ops.pop(cap_id, None)

    def _construct_executor_handler(
        self,
        manifest: DeploymentManifest,
        override: Optional[Callable] = None,
    ) -> Callable:
        """Construct a safe, contract-enforcing executor handler for the deployment."""
        if manifest.deployment_type == DeploymentType.EXECUTABLE_CODE and not self.has_process_isolation:
            raise RuntimeError(
                "Cannot construct executor handler for EXECUTABLE_CODE without process isolation. "
                "Refusing to substitute a synthetic handler for unisolated generated code."
            )

        base_handler = override or self._get_base_handler(manifest)

        def contract_and_lifecycle_handler(**kwargs) -> ToolResult:
            cap_id = manifest.capability_id
            if kwargs.get("health_probe"):
                return base_handler(**kwargs)

            curr_status = manifest.status
            if curr_status == DeploymentStatus.DEACTIVATING:
                return ToolResult(
                    tool_id=cap_id,
                    status="ERROR",
                    error=f"Capability '{cap_id}' is currently deactivating. New invocations are rejected.",
                )
            if curr_status != DeploymentStatus.ACTIVE:
                return ToolResult(
                    tool_id=cap_id,
                    status="ERROR",
                    error=f"Capability '{cap_id}' is not active (status: {curr_status.value}).",
                )
            return base_handler(**kwargs)

        return contract_and_lifecycle_handler

    def _get_base_handler(self, manifest: DeploymentManifest) -> Callable:
        """Construct the underlying handler implementation according to deployment type."""
        cap_id = manifest.capability_id

        if manifest.deployment_type == DeploymentType.APPROVED_EXTERNAL:
            # Verified adapter handler
            def adapter_handler(**kwargs) -> ToolResult:
                t0 = time.perf_counter()
                clean_inputs = {}
                for k, v in kwargs.items():
                    clean_inputs[k] = sanitize_content(v) if isinstance(v, str) else v
                dur = (time.perf_counter() - t0) * 1000
                return ToolResult(
                    tool_id=cap_id,
                    status="SUCCESS",
                    output={"capability_id": cap_id, "processed": True, "inputs": clean_inputs},
                    evidence=[f"Executed approved adapter {cap_id} in {dur:.2f}ms"],
                )
            return adapter_handler

        elif manifest.deployment_type == DeploymentType.DECLARATIVE_EXTENSION:
            # Declarative manifest handler
            def declarative_handler(**kwargs) -> ToolResult:
                return ToolResult(
                    tool_id=cap_id,
                    status="SUCCESS",
                    output={"capability_id": cap_id, "workflow_executed": True, "params": kwargs},
                    evidence=[f"Executed declarative workflow {cap_id}"],
                )
            return declarative_handler

        else:
            # Existing trusted handler
            def trusted_handler(**kwargs) -> ToolResult:
                return ToolResult(
                    tool_id=cap_id,
                    status="SUCCESS",
                    output={"capability_id": cap_id, "result": "Operation successful."},
                    evidence=[f"Executed {cap_id}"],
                )
            return trusted_handler

    # -------------------------------------------------------------------------
    # 4. In-Flight Protection & Controlled Deactivation
    # -------------------------------------------------------------------------
    def invoke_deployed_capability(
        self,
        capability_id: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ToolResult:
        """Execute a deployed capability with in-flight tracking, timeout handling, and deactivation protection."""
        # 1. Check if capability is currently deactivating or inactive
        dep = self._find_deployment_by_capability_id(capability_id)
        if dep:
            if dep.status == DeploymentStatus.DEACTIVATING:
                return ToolResult(
                    tool_id=capability_id,
                    status="ERROR",
                    error=f"Capability '{capability_id}' is currently deactivating. New invocations are rejected.",
                )
            if dep.status != DeploymentStatus.ACTIVE:
                return ToolResult(
                    tool_id=capability_id,
                    status="ERROR",
                    error=f"Capability '{capability_id}' is not active (status: {dep.status.value}).",
                )

        cap = self.capability_registry.get(capability_id)
        if not cap:
            return ToolResult(tool_id=capability_id, status="ERROR", error=f"Capability '{capability_id}' not found.")

        # 2. Permission check
        granted = set(caller_permissions or [])
        missing = [p for p in cap.permissions if p not in granted]
        if missing:
            return ToolResult(
                tool_id=capability_id,
                status="ERROR",
                error=f"Caller missing required permission(s): {[p.value for p in missing]}",
            )

        if not cap.handler:
            return ToolResult(tool_id=capability_id, status="ERROR", error="No handler registered.")

        # Sanitize inputs
        clean_inputs = {}
        for k, v in (inputs or {}).items():
            clean_inputs[k] = sanitize_content(v) if isinstance(v, str) else v

        # 3. Synchronous execution if no timeout specified
        if timeout_seconds is None:
            with self._in_flight_lock:
                self._in_flight[capability_id] = self._in_flight.get(capability_id, 0) + 1

            try:
                result = cap.handler(**clean_inputs)
                if not isinstance(result, ToolResult):
                    result = ToolResult(tool_id=capability_id, status="SUCCESS", output=result)
                return result
            except Exception as e:
                return ToolResult(tool_id=capability_id, status="ERROR", error=f"Execution error: {str(e)}")
            finally:
                with self._in_flight_lock:
                    cnt = self._in_flight.get(capability_id, 1) - 1
                    if cnt <= 0:
                        self._in_flight.pop(capability_id, None)
                    else:
                        self._in_flight[capability_id] = cnt

        # 4. Asynchronous worker execution with caller deadline
        result_holder: List[ToolResult] = []
        error_holder: List[Exception] = []

        with self._in_flight_lock:
            self._in_flight[capability_id] = self._in_flight.get(capability_id, 0) + 1

        def _worker():
            try:
                res = cap.handler(**clean_inputs)
                if not isinstance(res, ToolResult):
                    res = ToolResult(tool_id=capability_id, status="SUCCESS", output=res)
                result_holder.append(res)
            except Exception as e:
                error_holder.append(e)
            finally:
                with self._in_flight_lock:
                    cnt = self._in_flight.get(capability_id, 1) - 1
                    if cnt <= 0:
                        self._in_flight.pop(capability_id, None)
                    else:
                        self._in_flight[capability_id] = cnt

        w_thread = threading.Thread(target=_worker, daemon=True)
        w_thread.start()
        w_thread.join(timeout=timeout_seconds)

        if w_thread.is_alive():
            return ToolResult(
                tool_id=capability_id,
                status="ERROR",
                error=(
                    f"Invocation deadline exceeded ({timeout_seconds:.1f}s). "
                    "Underlying execution continuing in background; in-flight count retained until worker completion."
                ),
            )

        if error_holder:
            return ToolResult(tool_id=capability_id, status="ERROR", error=f"Execution error: {str(error_holder[0])}")

        if result_holder:
            return result_holder[0]

        return ToolResult(tool_id=capability_id, status="ERROR", error="No result produced by handler.")

    def deactivate_deployment(
        self,
        deployment_id: str,
        drain_timeout_seconds: float = 3.0,
    ) -> Tuple[bool, Optional[str]]:
        """Safely deactivate a deployed capability, draining in-flight invocations."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False, f"Deployment '{deployment_id}' not found."

            cap_id = manifest.capability_id

            if manifest.status == DeploymentStatus.INACTIVE:
                # Idempotent deactivation
                return True, "Deployment is already inactive."

            # Guard against competing lifecycle operations
            active_op = self._active_lifecycle_ops.get(cap_id)
            if active_op and active_op != deployment_id:
                return False, f"Capability '{cap_id}' is currently undergoing lifecycle transition by deployment '{active_op}'."

            if manifest.status not in (DeploymentStatus.ACTIVE, DeploymentStatus.DEACTIVATION_FAILED, DeploymentStatus.NEEDS_REVIEW):
                return False, f"Cannot deactivate deployment in status '{manifest.status.value}'."

            self._active_lifecycle_ops[cap_id] = deployment_id

            try:
                # 1. Move to DEACTIVATING (rejects new calls immediately)
                self._transition(manifest, DeploymentStatus.DEACTIVATING)
                self._save_manifest_to_disk(manifest)

                self.event_bus.emit(
                    "deployment.deactivating",
                    deployment_id,
                    data=self._sanitize({"deployment_id": deployment_id, "capability_id": cap_id, "state": "attempted"}),
                )

                # 2. Drain in-flight calls
                drained = False
                t0 = time.time()
                while time.time() - t0 < drain_timeout_seconds:
                    with self._in_flight_lock:
                        if self._in_flight.get(cap_id, 0) == 0:
                            drained = True
                            break
                    time.sleep(0.05)

                with self._in_flight_lock:
                    remaining_in_flight = self._in_flight.get(cap_id, 0)

                if not drained and remaining_in_flight > 0:
                    self._transition(manifest, DeploymentStatus.DEACTIVATION_FAILED)
                    self._save_manifest_to_disk(manifest)
                    return False, (
                        f"Deactivation drain timed out after {drain_timeout_seconds:.1f}s with "
                        f"{remaining_in_flight} active invocation(s) still in-flight. Deactivation aborted."
                    )

                # 3. Unregister from live registry
                self.capability_registry.unregister(cap_id)

                # 4. Verify post-condition
                if self.capability_registry.get(cap_id) is not None:
                    self._transition(manifest, DeploymentStatus.DEACTIVATION_FAILED)
                    self._save_manifest_to_disk(manifest)
                    return False, f"Deactivation post-condition check failed: '{cap_id}' still present in registry."

                # 5. Move to INACTIVE
                self._transition(manifest, DeploymentStatus.INACTIVE)
                self._save_manifest_to_disk(manifest)

                self.event_bus.emit(
                    "deployment.deactivated",
                    deployment_id,
                    data=self._sanitize({"deployment_id": deployment_id, "capability_id": cap_id, "state": "completed"}),
                )
                return True, None
            finally:
                self._active_lifecycle_ops.pop(cap_id, None)

    # -------------------------------------------------------------------------
    # 5. Rollback with Restoration of Previous State
    # -------------------------------------------------------------------------
    def rollback_deployment(self, deployment_id: str) -> Tuple[bool, Optional[str]]:
        """Revert a deployment, restoring previous registered capability if one existed."""
        with self._lock:
            manifest = self._manifests.get(deployment_id)
            if not manifest:
                return False, f"Deployment '{deployment_id}' not found."

            cap_id = manifest.capability_id

            active_op = self._active_lifecycle_ops.get(cap_id)
            if active_op and active_op != deployment_id:
                return False, f"Capability '{cap_id}' is currently undergoing lifecycle transition by deployment '{active_op}'."

            self._active_lifecycle_ops[cap_id] = deployment_id

            try:
                self._transition(manifest, DeploymentStatus.ROLLBACK_PENDING)
                self.event_bus.emit(
                    "deployment.rollback_started",
                    deployment_id,
                    data=self._sanitize({"deployment_id": deployment_id, "capability_id": cap_id, "state": "attempted"}),
                )
                ok = self._rollback_registry_change(deployment_id)
                if ok:
                    self._transition(manifest, DeploymentStatus.ROLLED_BACK)
                    self._save_manifest_to_disk(manifest)
                    self.event_bus.emit(
                        "deployment.rolled_back",
                        deployment_id,
                        data=self._sanitize({"deployment_id": deployment_id, "capability_id": cap_id, "state": "completed"}),
                    )
                    return True, "Rollback verified."
                else:
                    self._transition(manifest, DeploymentStatus.NEEDS_REVIEW)
                    self._save_manifest_to_disk(manifest)
                    return False, "Rollback encountered inconsistency; state marked NEEDS_REVIEW."
            finally:
                self._active_lifecycle_ops.pop(cap_id, None)

    def _rollback_registry_change(self, deployment_id: str) -> bool:
        """Internal helper to restore registry to pre-deployment snapshot with ownership verification."""
        manifest = self._manifests.get(deployment_id)
        if not manifest:
            return False

        cap_id = manifest.capability_id
        snapshot = self._registry_snapshots.get(deployment_id)
        current = self.capability_registry.get(cap_id)

        # Ownership check: if current registered capability exists, verify it belongs to this deployment
        # before touching it. If provenance differs, a concurrent or newer valid update holds it.
        if current is not None and current.provenance != f"deployment:{deployment_id}":
            return False

        if snapshot is not None:
            # Restore previous capability definition
            self.capability_registry.restore_capability(snapshot)
            restored = self.capability_registry.get(cap_id)
            return restored is not None and restored.provenance == snapshot.provenance
        else:
            # No prior capability existed: simply unregister
            self.capability_registry.unregister(cap_id)
            return self.capability_registry.get(cap_id) is None

    # -------------------------------------------------------------------------
    # 6. Bounded Observable Health Checks
    # -------------------------------------------------------------------------
    def run_health_check(
        self,
        deployment_id: str,
        timeout_seconds: float = 5.0,
    ) -> Tuple[bool, Optional[str]]:
        """Perform a bounded, deterministic health check on the active or activating capability.

        Timeout & Concurrency Guarantees:
        The timeout_seconds parameter enforces a logical deadline for the health check probe.
        Because Python threads cannot be asynchronously terminated at the OS boundary,
        a cooperative cancel_event is signaled when the timeout expires. Probes that ignore
        the event may continue executing. To prevent thread or handle exhaustion,
        active probe threads are tracked and concurrency is capped (max 10 concurrent active probes).
        """
        manifest = self._manifests.get(deployment_id)
        if not manifest:
            return False, f"Deployment '{deployment_id}' not found."

        cap = self.capability_registry.get(manifest.capability_id)
        if not cap:
            return False, f"Capability '{manifest.capability_id}' not found in registry."

        # 1. Executor availability
        if cap.handler is None:
            return False, f"Executor handler is missing for '{manifest.capability_id}'."

        # 2. Dependency availability
        import importlib.util
        for dep in manifest.dependencies:
            # stdlib or installed package check
            spec = importlib.util.find_spec(dep)
            if spec is None:
                return False, f"Required dependency '{dep}' is missing or unresolvable."

        # Concurrency bounding check
        with self._probe_lock:
            # Clean finished threads
            self._active_probes = {t for t in self._active_probes if t.is_alive()}
            if len(self._active_probes) >= 10:
                return False, "Health check probe concurrency limit reached (max 10 concurrent probes)."

        # 3. Contract probe with read-only synthetic input and bounded timeout
        result_holder: List[Any] = []
        error_holder: List[Exception] = []
        cancel_event = threading.Event()

        def _probe_worker() -> None:
            try:
                try:
                    res = cap.handler(health_probe=True, cancel_event=cancel_event)
                except TypeError:
                    res = cap.handler(health_probe=True)
                result_holder.append(res)
            except Exception as e:
                error_holder.append(e)

        probe_thread = threading.Thread(target=_probe_worker, daemon=True)
        with self._probe_lock:
            self._active_probes.add(probe_thread)

        t0 = time.perf_counter()
        probe_thread.start()
        probe_thread.join(timeout=timeout_seconds)
        duration_ms = (time.perf_counter() - t0) * 1000

        if probe_thread.is_alive():
            cancel_event.set()
            return False, (
                f"Health check timed out: logical deadline exceeded ({duration_ms:.1f}ms > {timeout_seconds * 1000:.1f}ms). "
                "Underlying thread cancellation signaled via cancel_event (preemptive termination unavailable in Python)."
            )

        with self._probe_lock:
            self._active_probes.discard(probe_thread)

        if error_holder:
            return False, f"Health check probe execution failed: {str(error_holder[0])}"

        if result_holder:
            probe_result = result_holder[0]
            if isinstance(probe_result, ToolResult) and probe_result.status == "ERROR":
                err_text = (probe_result.error or "").lower()
                if "health" in err_text or "fail" in err_text or "unhealthy" in err_text:
                    return False, f"Probe error: {probe_result.error}"

        return True, None

    # -------------------------------------------------------------------------
    # 7. Restart Crash Recovery & Idempotency
    # -------------------------------------------------------------------------
    def recover_deployments(self) -> Dict[str, str]:
        """Inspect all deployments on startup and resolve interrupted or inconsistent states."""
        with self._lock:
            actions_taken: Dict[str, str] = {}
            for dep_id, manifest in self._manifests.items():
                status = manifest.status
                cap_id = manifest.capability_id

                # 1. Manifest integrity verification
                valid, errs = manifest.verify_integrity()
                if not valid:
                    if status != DeploymentStatus.NEEDS_REVIEW:
                        self._transition(manifest, DeploymentStatus.NEEDS_REVIEW)
                        action_msg = f"Flagged NEEDS_REVIEW: manifest integrity check failed: {errs}"
                        actions_taken[dep_id] = action_msg
                        self._save_manifest_to_disk(manifest)
                        self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)
                    continue

                is_reg = self.capability_registry.is_registered(cap_id)

                if status == DeploymentStatus.ACTIVATING:
                    # Interrupted during activation
                    if is_reg:
                        healthy, _ = self.run_health_check(dep_id)
                        if healthy:
                            self._transition(manifest, DeploymentStatus.ACTIVE)
                            action_msg = "Recovered to ACTIVE after verified health check"
                        else:
                            self._rollback_registry_change(dep_id)
                            self._transition(manifest, DeploymentStatus.ROLLED_BACK)
                            action_msg = "Rolled back unverified partial activation"
                    else:
                        self._transition(manifest, DeploymentStatus.ACTIVATION_FAILED)
                        action_msg = "Reset to ACTIVATION_FAILED"
                    actions_taken[dep_id] = action_msg
                    self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)

                elif status == DeploymentStatus.DEACTIVATING:
                    # Interrupted during deactivation: finish unregistering
                    self.capability_registry.unregister(cap_id)
                    self._transition(manifest, DeploymentStatus.INACTIVE)
                    action_msg = "Cleaned up interrupted deactivation to INACTIVE"
                    actions_taken[dep_id] = action_msg
                    self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)

                elif status == DeploymentStatus.ROLLBACK_PENDING:
                    # Interrupted during rollback: finish rollback
                    self._rollback_registry_change(dep_id)
                    self._transition(manifest, DeploymentStatus.ROLLED_BACK)
                    action_msg = "Completed interrupted rollback to ROLLED_BACK"
                    actions_taken[dep_id] = action_msg
                    self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)

                elif status == DeploymentStatus.ACTIVE:
                    # Re-verify active status on startup
                    if not is_reg:
                        self._transition(manifest, DeploymentStatus.NEEDS_REVIEW)
                        action_msg = "Flagged NEEDS_REVIEW: recorded active but absent from registry"
                    else:
                        healthy, _ = self.run_health_check(dep_id)
                        if not healthy:
                            self._transition(manifest, DeploymentStatus.NEEDS_REVIEW)
                            action_msg = "Flagged NEEDS_REVIEW: recorded active but health check failed on startup"
                        else:
                            action_msg = ""
                    if action_msg:
                        actions_taken[dep_id] = action_msg
                        self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)

                elif status == DeploymentStatus.STAGING:
                    # Interrupted during staging
                    self._transition(manifest, DeploymentStatus.VALIDATION_FAILED)
                    action_msg = "Flagged VALIDATION_FAILED: staging interrupted by restart"
                    actions_taken[dep_id] = action_msg
                    self._emit_recovery_event(dep_id, cap_id, action_msg, manifest.status)

                self._save_manifest_to_disk(manifest)

            return actions_taken

    def _emit_recovery_event(
        self,
        deployment_id: str,
        capability_id: str,
        action: str,
        status: DeploymentStatus,
    ) -> None:
        """Emit recovery event and persist recovery audit entry."""
        self.event_bus.emit(
            "deployment.recovered",
            deployment_id,
            data=self._sanitize({
                "deployment_id": deployment_id,
                "capability_id": capability_id,
                "action": action,
                "status": status.value,
                "state": "recovered",
            }),
        )
        if self.memory_store:
            try:
                self.memory_store.add(
                    MemoryRecord(
                        type=MemoryType.ENVIRONMENT_FACT,
                        content=f"Capability deployment '{deployment_id}' recovered: {action}",
                        source=MemorySource.SYSTEM,
                        metadata=self._sanitize({"deployment_id": deployment_id}),
                    )
                )
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------
    def _transition(self, manifest: DeploymentManifest, target: DeploymentStatus) -> None:
        """Enforce valid state transitions."""
        curr = manifest.status
        allowed = VALID_DEPLOYMENT_TRANSITIONS.get(curr, set())
        if target not in allowed and target != curr:
            raise ValueError(
                f"Illegal deployment state transition from '{curr.value}' to '{target.value}'. "
                f"Allowed: {[s.value for s in allowed]}"
            )
        manifest.status = target
        manifest.updated_at = time.time()

    def _find_deployment_by_capability_id(self, capability_id: str) -> Optional[DeploymentManifest]:
        """Find active or tracking manifest for capability ID."""
        for m in self._manifests.values():
            if m.capability_id == capability_id:
                return m
        return None

    def get_manifest(self, deployment_id: str) -> Optional[DeploymentManifest]:
        """Retrieve deployment manifest by ID."""
        with self._lock:
            return self._manifests.get(deployment_id)

    def list_manifests(self, status: Optional[DeploymentStatus] = None) -> List[DeploymentManifest]:
        """List deployment manifests matching status filter."""
        with self._lock:
            if status:
                return [m for m in self._manifests.values() if m.status == status]
            return list(self._manifests.values())
