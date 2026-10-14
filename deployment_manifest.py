"""Immutable Deployment Manifest and Deterministic Lifecycle Model (Phase 25).

Defines the formal deployment specification and state machine for capabilities
across all lifecycle stages:
CANDIDATE -> STAGING -> STAGED -> READY_FOR_APPROVAL -> APPROVED -> ACTIVATING -> ACTIVE -> DEACTIVATING -> INACTIVE
plus failure and recovery states:
VALIDATION_FAILED, BLOCKED_ISOLATION, BLOCKED_PERMISSION, ACTIVATION_FAILED, DEACTIVATION_FAILED, ROLLBACK_PENDING, ROLLED_BACK, NEEDS_REVIEW.

Key Invariants:
1. Manifest immutability: cryptographically bound via SHA-256 manifest hash.
   Material changes invalidate prior approvals and validation evidence.
2. Capability tier classification distinguishes existing trusted tools, approved external
   adapters, declarative extensions, and executable code.
3. Fail-closed on executable code: unisolated generated code is never marked ACTIVE.
4. Deterministic state transitions strictly enforced.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission


class DeploymentType(str, Enum):
    """Architectural classification of a deployed capability."""
    EXISTING_TRUSTED = "EXISTING_TRUSTED"          # Built-in tool or skill with existing implementation
    APPROVED_EXTERNAL = "APPROVED_EXTERNAL"        # Approved external integration with supported adapter
    DECLARATIVE_EXTENSION = "DECLARATIVE_EXTENSION"# Non-executable manifest / workflow description
    EXECUTABLE_CODE = "EXECUTABLE_CODE"            # Generated / third-party code requiring OS isolation


class DeploymentStatus(str, Enum):
    """Deterministic lifecycle states of a capability deployment."""
    CANDIDATE = "CANDIDATE"                        # Initial manifest prepared from proposal
    STAGING = "STAGING"                            # Staged in isolated staging runtime
    STAGED = "STAGED"                              # Staging verified; ready for review
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"      # Review package ready for human inspection
    APPROVED = "APPROVED"                          # Human confirmation granted bound to manifest hash
    ACTIVATING = "ACTIVATING"                      # Transactional activation in progress
    ACTIVE = "ACTIVE"                              # Registered in live CapabilityRegistry & healthy
    DEACTIVATING = "DEACTIVATING"                  # Draining in-flight calls and unregistering
    INACTIVE = "INACTIVE"                          # Cleanly deactivated and removed from live registry

    # Failure & Recovery States
    VALIDATION_FAILED = "VALIDATION_FAILED"        # Validation checks failed
    BLOCKED_ISOLATION = "BLOCKED_ISOLATION"        # Executable code blocked due to lack of OS isolation
    BLOCKED_PERMISSION = "BLOCKED_PERMISSION"      # Required permissions or approval denied
    ACTIVATION_FAILED = "ACTIVATION_FAILED"        # Error or crash during activation
    DEACTIVATION_FAILED = "DEACTIVATION_FAILED"    # Error during deactivation
    ROLLBACK_PENDING = "ROLLBACK_PENDING"          # Rollback initiated following activation failure
    ROLLED_BACK = "ROLLED_BACK"                    # Previous state restored and verified
    NEEDS_REVIEW = "NEEDS_REVIEW"                  # Inconsistent state detected on restart recovery


# Deterministic state transition graph
VALID_DEPLOYMENT_TRANSITIONS: Dict[DeploymentStatus, Set[DeploymentStatus]] = {
    DeploymentStatus.CANDIDATE: {
        DeploymentStatus.STAGING,
        DeploymentStatus.BLOCKED_ISOLATION,
        DeploymentStatus.VALIDATION_FAILED,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.STAGING: {
        DeploymentStatus.STAGED,
        DeploymentStatus.VALIDATION_FAILED,
        DeploymentStatus.BLOCKED_ISOLATION,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.STAGED: {
        DeploymentStatus.READY_FOR_APPROVAL,
        DeploymentStatus.VALIDATION_FAILED,
        DeploymentStatus.BLOCKED_ISOLATION,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.READY_FOR_APPROVAL: {
        DeploymentStatus.APPROVED,
        DeploymentStatus.BLOCKED_PERMISSION,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.APPROVED: {
        DeploymentStatus.ACTIVATING,
        DeploymentStatus.BLOCKED_ISOLATION,
        DeploymentStatus.READY_FOR_APPROVAL,  # Invalidation returns to approval state
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.ACTIVATING: {
        DeploymentStatus.ACTIVE,
        DeploymentStatus.ACTIVATION_FAILED,
        DeploymentStatus.ROLLBACK_PENDING,
        DeploymentStatus.NEEDS_REVIEW,
    },
    DeploymentStatus.ACTIVE: {
        DeploymentStatus.DEACTIVATING,
        DeploymentStatus.ROLLBACK_PENDING,
        DeploymentStatus.NEEDS_REVIEW,
    },
    DeploymentStatus.DEACTIVATING: {
        DeploymentStatus.INACTIVE,
        DeploymentStatus.DEACTIVATION_FAILED,
        DeploymentStatus.NEEDS_REVIEW,
    },
    DeploymentStatus.INACTIVE: {
        DeploymentStatus.CANDIDATE,  # Re-deployment path
        DeploymentStatus.NEEDS_REVIEW,
    },
    # Recovery and Failure Transitions
    DeploymentStatus.VALIDATION_FAILED: {
        DeploymentStatus.CANDIDATE,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.BLOCKED_ISOLATION: {
        DeploymentStatus.CANDIDATE,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.BLOCKED_PERMISSION: {
        DeploymentStatus.READY_FOR_APPROVAL,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.ACTIVATION_FAILED: {
        DeploymentStatus.ROLLBACK_PENDING,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.DEACTIVATION_FAILED: {
        DeploymentStatus.DEACTIVATING,  # Retry deactivation
        DeploymentStatus.NEEDS_REVIEW,
    },
    DeploymentStatus.ROLLBACK_PENDING: {
        DeploymentStatus.ROLLED_BACK,
        DeploymentStatus.NEEDS_REVIEW,
    },
    DeploymentStatus.ROLLED_BACK: {
        DeploymentStatus.CANDIDATE,
        DeploymentStatus.NEEDS_REVIEW,
        DeploymentStatus.INACTIVE,
    },
    DeploymentStatus.NEEDS_REVIEW: {
        DeploymentStatus.DEACTIVATING,
        DeploymentStatus.ROLLBACK_PENDING,
        DeploymentStatus.INACTIVE,
    },
}


@dataclass
class DeploymentManifest:
    """Immutable specification defining a capability deployment."""
    deployment_id: str
    capability_id: str
    name: str
    description: str
    proposal_id: str
    candidate_hash: str
    version: str = "1.0.0"
    provenance: str = "builtin"
    deployment_type: DeploymentType = DeploymentType.EXISTING_TRUSTED
    supported_outcomes: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    runtime_requirements: Dict[str, Any] = field(default_factory=dict)
    input_contract: Dict[str, Any] = field(default_factory=dict)
    output_contract: Dict[str, Any] = field(default_factory=dict)
    required_permissions: List[TaskPermission] = field(default_factory=list)
    artifact_hashes: Dict[str, str] = field(default_factory=dict)  # relative_path -> sha256
    validation_summary: Dict[str, Any] = field(default_factory=dict)
    approval_record: Optional[Dict[str, Any]] = None
    health_check_spec: Dict[str, Any] = field(default_factory=dict)
    activation_procedure: str = "standard_registry_transaction"
    deactivation_procedure: str = "standard_drain_and_unregister"
    rollback_plan: str = "restore_previous_registry_snapshot"
    status: DeploymentStatus = DeploymentStatus.CANDIDATE
    manifest_hash: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.deployment_type, str):
            try:
                self.deployment_type = DeploymentType(self.deployment_type)
            except ValueError:
                self.deployment_type = DeploymentType.EXISTING_TRUSTED
        if isinstance(self.status, str):
            try:
                self.status = DeploymentStatus(self.status)
            except ValueError:
                self.status = DeploymentStatus.CANDIDATE

        # Normalize permissions
        normalized: List[TaskPermission] = []
        for p in self.required_permissions:
            if isinstance(p, str):
                try:
                    normalized.append(TaskPermission(p))
                except ValueError:
                    pass
            elif isinstance(p, TaskPermission):
                normalized.append(p)
        self.required_permissions = normalized

        # Compute deterministic manifest hash if not already computed
        if not self.manifest_hash:
            self.manifest_hash = self.compute_manifest_hash()

    def compute_manifest_hash(self) -> str:
        """Compute deterministic SHA-256 fingerprint over all material deployment attributes."""
        payload = {
            "deployment_id": self.deployment_id,
            "capability_id": self.capability_id,
            "version": self.version,
            "candidate_hash": self.candidate_hash,
            "deployment_type": self.deployment_type.value,
            "dependencies": sorted(self.dependencies),
            "permissions": sorted([p.value for p in self.required_permissions]),
            "input_contract": self.input_contract,
            "output_contract": self.output_contract,
            "artifact_hashes": {k: self.artifact_hashes[k] for k in sorted(self.artifact_hashes.keys())},
        }
        raw = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """Verify that manifest attributes match the computed fingerprint."""
        errors: List[str] = []
        expected_hash = self.compute_manifest_hash()
        if self.manifest_hash != expected_hash:
            errors.append(
                f"Manifest fingerprint mismatch: recorded '{self.manifest_hash[:16]}...' "
                f"!= computed '{expected_hash[:16]}...'. Manifest was materially mutated."
            )
        if not self.capability_id:
            errors.append("Capability ID cannot be empty.")
        if not self.version:
            errors.append("Version cannot be empty.")
        return len(errors) == 0, errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "proposal_id": self.proposal_id,
            "candidate_hash": self.candidate_hash,
            "version": self.version,
            "provenance": self.provenance,
            "deployment_type": self.deployment_type.value,
            "supported_outcomes": list(self.supported_outcomes),
            "dependencies": list(self.dependencies),
            "runtime_requirements": dict(self.runtime_requirements),
            "input_contract": dict(self.input_contract),
            "output_contract": dict(self.output_contract),
            "required_permissions": [p.value for p in self.required_permissions],
            "artifact_hashes": dict(self.artifact_hashes),
            "validation_summary": dict(self.validation_summary),
            "approval_record": dict(self.approval_record) if self.approval_record else None,
            "health_check_spec": dict(self.health_check_spec),
            "activation_procedure": self.activation_procedure,
            "deactivation_procedure": self.deactivation_procedure,
            "rollback_plan": self.rollback_plan,
            "status": self.status.value,
            "manifest_hash": self.manifest_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeploymentManifest:
        clean = dict(data)
        if "deployment_type" in clean:
            clean["deployment_type"] = DeploymentType(clean["deployment_type"])
        if "status" in clean:
            clean["status"] = DeploymentStatus(clean["status"])
        if "required_permissions" in clean:
            clean["required_permissions"] = [TaskPermission(p) for p in clean["required_permissions"]]
        return cls(**clean)
