"""Structured Extension Proposal Model and Lifecycle State Machine for Hey Jev (Phase 23).

Enforces an immutable, verifiable lifecycle for prospective capability acquisitions:
DETECTED -> INVESTIGATING -> PROPOSED -> VALIDATING -> AWAITING_APPROVAL
-> APPROVED / REJECTED -> INSTALLING -> VERIFYING -> ACTIVE / FAILED

Enforces strict separation:
- Discovery does NOT grant permissions.
- Proposal creation does NOT grant permissions.
- Validation does NOT grant permissions.
- Material candidate mutations strictly trigger re-approval.
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
from integration_discovery import CandidateExtension


class ProposalStatus(str, Enum):
    """Lifecycle states of an extension proposal."""
    DETECTED = "DETECTED"                  # Gap observed, candidate acquisition flagged
    INVESTIGATING = "INVESTIGATING"        # Candidate details, provenance, and risks being analyzed
    PROPOSED = "PROPOSED"                  # Structured proposal formed with rationale & rollback plan
    VALIDATING = "VALIDATING"              # Running bounded static/isolated validation checks
    AWAITING_APPROVAL = "AWAITING_APPROVAL"# Validation passed, halted waiting for explicit human authorization
    APPROVED = "APPROVED"                  # Human confirmed scoped authorization
    REJECTED = "REJECTED"                  # Human or policy explicitly declined proposal (terminal)
    INSTALLING = "INSTALLING"              # Registering/staging files into sandbox/isolated runtime
    VERIFYING = "VERIFYING"                # Verifying operational health post-install
    ACTIVE = "ACTIVE"                      # Fully verified, registered in CapabilityRegistry (terminal)
    FAILED = "FAILED"                      # Failed during validation, install, or verification (terminal)


# Legal state transition graph
VALID_PROPOSAL_TRANSITIONS: Dict[ProposalStatus, Set[ProposalStatus]] = {
    ProposalStatus.DETECTED: {
        ProposalStatus.INVESTIGATING,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
    },
    ProposalStatus.INVESTIGATING: {
        ProposalStatus.PROPOSED,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
    },
    ProposalStatus.PROPOSED: {
        ProposalStatus.VALIDATING,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
    },
    ProposalStatus.VALIDATING: {
        ProposalStatus.AWAITING_APPROVAL,
        ProposalStatus.FAILED,
        ProposalStatus.REJECTED,
    },
    ProposalStatus.AWAITING_APPROVAL: {
        ProposalStatus.APPROVED,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
    },
    ProposalStatus.APPROVED: {
        ProposalStatus.INSTALLING,
        ProposalStatus.VERIFYING,
        ProposalStatus.ACTIVE,
        ProposalStatus.REJECTED,
        ProposalStatus.FAILED,
    },
    ProposalStatus.INSTALLING: {
        ProposalStatus.VERIFYING,
        ProposalStatus.FAILED,
        ProposalStatus.REJECTED,
    },
    ProposalStatus.VERIFYING: {
        ProposalStatus.ACTIVE,
        ProposalStatus.FAILED,
        ProposalStatus.REJECTED,
    },
    ProposalStatus.ACTIVE: set(),    # Terminal state
    ProposalStatus.REJECTED: set(),  # Terminal state
    ProposalStatus.FAILED: set(),    # Terminal state
}


@dataclass
class ExtensionProposal:
    """A formal, reviewable proposal to extend Hey Jev capabilities."""
    proposal_id: str
    user_goal: str
    gap_id: str
    candidate: CandidateExtension
    status: ProposalStatus = ProposalStatus.DETECTED
    rationale: str = ""
    alternatives_considered: List[str] = field(default_factory=list)
    requested_permissions: List[TaskPermission] = field(default_factory=list)
    security_implications: List[str] = field(default_factory=list)
    validation_plan: Dict[str, Any] = field(default_factory=dict)
    sandbox_strategy: str = "isolated_temp_sandbox_synthetic_data"
    rollback_plan: str = "Remove staged files and unregister capability immediately"
    approval_required: bool = True
    approval_state: str = "PENDING"  # "PENDING", "APPROVED", "REJECTED"
    approval_grant: Optional[Dict[str, Any]] = None
    validation_evidence: Optional[Dict[str, Any]] = None
    candidate_hash: str = ""
    rejection_reason: str = ""
    failure_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.candidate_hash and self.candidate:
            self.candidate_hash = self.compute_candidate_hash(self.candidate)
        if not self.requested_permissions and self.candidate:
            self.requested_permissions = list(self.candidate.required_permissions)

    @staticmethod
    def compute_candidate_hash(candidate: CandidateExtension) -> str:
        """Compute deterministic SHA-256 fingerprint of candidate to detect mutations."""
        data = {
            "id": candidate.candidate_id,
            "version": candidate.version,
            "publisher": candidate.publisher,
            "dependencies": sorted(candidate.dependencies),
            "permissions": sorted([p.value for p in candidate.required_permissions]),
            "spec": candidate.spec,
        }
        raw = json.dumps(data, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status.value,
            "user_goal": self.user_goal,
            "gap_id": self.gap_id,
            "candidate": self.candidate.to_dict(),
            "rationale": self.rationale,
            "alternatives_considered": list(self.alternatives_considered),
            "requested_permissions": [p.value for p in self.requested_permissions],
            "security_implications": list(self.security_implications),
            "validation_plan": dict(self.validation_plan),
            "sandbox_strategy": self.sandbox_strategy,
            "rollback_plan": self.rollback_plan,
            "approval_required": self.approval_required,
            "approval_state": self.approval_state,
            "approval_grant": self.approval_grant,
            "validation_evidence": self.validation_evidence,
            "candidate_hash": self.candidate_hash,
            "rejection_reason": self.rejection_reason,
            "failure_reason": self.failure_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ProposalLifecycleManager:
    """Enforces legal lifecycle transitions and security re-approval rules."""

    @staticmethod
    def transition(proposal: ExtensionProposal, target_status: ProposalStatus, reason: str = "") -> bool:
        """Attempt to transition proposal to a new lifecycle state.
        
        Returns True if transition succeeded, raises ValueError on illegal transitions.
        """
        curr = proposal.status
        allowed = VALID_PROPOSAL_TRANSITIONS.get(curr, set())

        if target_status not in allowed:
            raise ValueError(
                f"Illegal proposal state transition from '{curr.value}' to '{target_status.value}'. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

        # Enforce prerequisite state rules
        if target_status == ProposalStatus.APPROVED:
            if proposal.approval_state != "APPROVED":
                raise ValueError("Cannot transition to APPROVED without a valid human approval grant.")
        elif target_status == ProposalStatus.ACTIVE:
            if proposal.status not in (ProposalStatus.APPROVED, ProposalStatus.VERIFYING):
                raise ValueError("Cannot transition to ACTIVE without passing validation and approval.")
            if proposal.approval_state != "APPROVED":
                raise ValueError("Cannot activate unapproved extension.")

        proposal.status = target_status
        proposal.updated_at = time.time()
        if target_status == ProposalStatus.REJECTED and reason:
            proposal.rejection_reason = reason
        elif target_status == ProposalStatus.FAILED and reason:
            proposal.failure_reason = reason

        return True

    @staticmethod
    def grant_approval(proposal: ExtensionProposal, approver: str = "user", scope_notes: str = "") -> bool:
        """Grant explicit human authorization scoped to this specific candidate version."""
        if proposal.status != ProposalStatus.AWAITING_APPROVAL:
            raise ValueError(
                f"Cannot approve proposal in status '{proposal.status.value}'. "
                f"Proposal must be in '{ProposalStatus.AWAITING_APPROVAL.value}'."
            )

        proposal.approval_state = "APPROVED"
        proposal.approval_grant = {
            "approver": approver,
            "timestamp": time.time(),
            "candidate_hash": proposal.candidate_hash,
            "candidate_version": proposal.candidate.version,
            "scoped_permissions": [p.value for p in proposal.requested_permissions],
            "notes": scope_notes,
        }
        return ProposalLifecycleManager.transition(proposal, ProposalStatus.APPROVED)

    @staticmethod
    def reject_proposal(proposal: ExtensionProposal, reason: str = "User declined proposal") -> bool:
        """Explicitly reject proposal, moving it to terminal REJECTED state."""
        proposal.approval_state = "REJECTED"
        return ProposalLifecycleManager.transition(proposal, ProposalStatus.REJECTED, reason=reason)

    @staticmethod
    def check_and_invalidate_on_material_change(
        proposal: ExtensionProposal,
        updated_candidate: CandidateExtension,
    ) -> bool:
        """Detect material candidate mutations (version, permissions, dependencies, spec).
        
        If mutations are detected on an already approved proposal, invalidate approval
        and return to AWAITING_APPROVAL.
        """
        new_hash = ExtensionProposal.compute_candidate_hash(updated_candidate)
        if new_hash != proposal.candidate_hash:
            # Material change detected
            proposal.candidate = updated_candidate
            proposal.candidate_hash = new_hash
            proposal.requested_permissions = list(updated_candidate.required_permissions)

            if proposal.approval_state == "APPROVED" or proposal.status in (
                ProposalStatus.APPROVED,
                ProposalStatus.INSTALLING,
                ProposalStatus.VERIFYING,
            ):
                # Invalidate approval grant
                proposal.approval_state = "PENDING"
                proposal.approval_grant = None
                proposal.status = ProposalStatus.AWAITING_APPROVAL
                proposal.updated_at = time.time()
                return True  # Re-approval required

        return False
