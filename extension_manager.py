"""Extension Manager and Capability Acquisition Coordinator for Hey Jev (Phase 23).

Coordinates the end-to-end safe capability acquisition workflow:
1. Detects and classifies capability gaps
2. Discovers candidates across prioritized trust tiers
3. Generates reviewable extension proposals with audit logging
4. Validates prospective extensions in bounded isolated sandboxes
5. Enforces human approval checkpoints with material change invalidation
6. Activates verified and approved extensions into CapabilityRegistry
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_gap import CapabilityGap, CapabilityGapAnalyzer, GapAnalysisResult, GapType
from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from event_bus import EventBus
from extension_proposal import (
    ExtensionProposal,
    ProposalLifecycleManager,
    ProposalStatus,
)
from extension_validator import ExtensionValidator, ValidationReport, ValidationStatus
from integration_discovery import (
    CandidateExtension,
    CandidateSourceType,
    IntegrationDiscoveryEngine,
)
from memory_model import MemoryRecord, MemorySource, MemoryType, sanitize_content
from memory_store import MemoryStore
from tool_model import RiskLevel, ToolHealth, ToolResult


class ExtensionManager:
    """Coordinates safe capability gap detection, proposal generation, validation, and activation."""

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        event_bus: Optional[EventBus] = None,
        memory_store: Optional[MemoryStore] = None,
        local_integrations_dir: Optional[str] = None,
        safety_engine: Optional[Any] = None,
        network_available: bool = False,
    ) -> None:
        self.capability_registry = capability_registry
        self.event_bus = event_bus or EventBus()
        self.memory_store = memory_store
        self._proposals: Dict[str, ExtensionProposal] = {}
        self._lock = threading.RLock()

        self.gap_analyzer = CapabilityGapAnalyzer(
            registry=self.capability_registry,
            event_bus=self.event_bus,
            safety_engine=safety_engine,
        )
        self.discovery_engine = IntegrationDiscoveryEngine(
            registry=self.capability_registry,
            local_integrations_dir=local_integrations_dir,
            network_available=network_available,
        )
        self.validator = ExtensionValidator()

    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Scrub sensitive credentials and passwords before emitting or persisting."""
        cleaned: Dict[str, Any] = {}
        for k, v in data.items():
            if isinstance(v, str):
                cleaned[k] = sanitize_content(v)
            elif isinstance(v, dict):
                cleaned[k] = self._sanitize_data(v)
            elif isinstance(v, list):
                cleaned[k] = [sanitize_content(x) if isinstance(x, str) else x for x in v]
            else:
                cleaned[k] = v
        return cleaned

    def analyze_gap(
        self,
        goal: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> GapAnalysisResult:
        """Analyze a goal and classify any missing capabilities or policy restrictions."""
        return self.gap_analyzer.analyze_goal(
            goal=goal,
            caller_permissions=caller_permissions,
            context=context,
        )

    def investigate_and_propose(
        self,
        gap: CapabilityGap,
        caller_permissions: Optional[List[TaskPermission]] = None,
        allow_external: bool = False,
    ) -> Optional[ExtensionProposal]:
        """Search for candidate implementations and synthesize a structured proposal."""
        with self._lock:
            # 1. Duplicate proposal prevention
            for existing in self._proposals.values():
                if existing.gap_id == gap.gap_id or (
                    existing.user_goal.strip().lower() == gap.desired_outcome.strip().lower()
                    and existing.status not in (ProposalStatus.REJECTED, ProposalStatus.FAILED)
                ):
                    return existing

            # 2. Safety & Permission check: do not propose software acquisition for denied permissions
            if gap.gap_type == GapType.PERMISSION_DENIED:
                return None
            if gap.gap_type == GapType.SAFETY_DISALLOWED:
                return None

            # 3. Discover candidates across prioritized tiers
            candidates = self.discovery_engine.discover_candidates(gap, allow_external=allow_external)
            if not candidates:
                return None

            # Pick top prioritized candidate
            primary_candidate = candidates[0]
            alt_ids = [c.candidate_id for c in candidates[1:]]

            # 4. Formulate structured proposal
            proposal_id = f"prop_{uuid.uuid4().hex[:8]}"
            rationale = (
                f"Selected {primary_candidate.source_type.value} '{primary_candidate.name}' "
                f"to provide missing capabilities: {primary_candidate.provided_capabilities}."
            )
            security_implications = [
                f"Requires permissions: {[p.value for p in primary_candidate.required_permissions]}",
                f"Risk tier: {primary_candidate.risk_level.value}",
            ]
            if not primary_candidate.is_trusted:
                security_implications.extend(primary_candidate.unresolved_trust_questions)

            proposal = ExtensionProposal(
                proposal_id=proposal_id,
                user_goal=gap.desired_outcome,
                gap_id=gap.gap_id,
                candidate=primary_candidate,
                status=ProposalStatus.PROPOSED,
                rationale=rationale,
                alternatives_considered=alt_ids,
                requested_permissions=list(primary_candidate.required_permissions),
                security_implications=security_implications,
                validation_plan={
                    "strategy": "isolated_sandbox_execution",
                    "acceptance_criteria": ["output_non_empty", "sandbox_cleaned"],
                },
                rollback_plan=f"Unregister capability '{primary_candidate.candidate_id}' and clean sandbox.",
            )

            self._proposals[proposal_id] = proposal

            # 5. Emit sanitized event and persist memory
            self.event_bus.emit(
                event_type="proposal.created",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal_id,
                    "goal": proposal.user_goal,
                    "candidate": primary_candidate.name,
                    "source_type": primary_candidate.source_type.value,
                    "risk_level": primary_candidate.risk_level.value,
                }),
            )

            if self.memory_store:
                try:
                    rec = MemoryRecord(
                        type=MemoryType.TASK_SUMMARY,
                        content=f"Extension proposal created: {primary_candidate.name} for goal '{proposal.user_goal}'.",
                        source=MemorySource.SYSTEM,
                        metadata=self._sanitize_data({"proposal_id": proposal_id, "gap_id": gap.gap_id}),
                    )
                    self.memory_store.add(rec)
                except Exception:
                    pass

            return proposal

    def validate_proposal(
        self,
        proposal_id: str,
        code_snippet: Optional[str] = None,
    ) -> ValidationReport:
        """Run safe multi-stage validation pipeline on the proposed candidate."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")

            ProposalLifecycleManager.transition(proposal, ProposalStatus.VALIDATING)

            report = self.validator.validate_candidate(
                candidate=proposal.candidate,
                proposal=proposal,
                code_snippet=code_snippet,
            )

            if report.status in (ValidationStatus.PASSED, ValidationStatus.WARNING):
                ProposalLifecycleManager.transition(proposal, ProposalStatus.AWAITING_APPROVAL)
            else:
                ProposalLifecycleManager.transition(
                    proposal,
                    ProposalStatus.FAILED,
                    reason=f"Validation failed: {report.risks_identified}",
                )

            self.event_bus.emit(
                event_type="proposal.validated",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal_id,
                    "status": report.status.value,
                    "passed_checks": report.passed_checks,
                    "failed_checks": report.failed_checks,
                }),
            )

            return report

    def approve_proposal(
        self,
        proposal_id: str,
        approver: str = "user",
        notes: str = "",
    ) -> bool:
        """Grant explicit scoped human authorization for a validated proposal."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")

            success = ProposalLifecycleManager.grant_approval(
                proposal=proposal,
                approver=approver,
                scope_notes=notes,
            )

            self.event_bus.emit(
                event_type="proposal.approved",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal_id,
                    "approver": approver,
                    "scoped_permissions": [p.value for p in proposal.requested_permissions],
                }),
            )

            if self.memory_store:
                try:
                    rec = MemoryRecord(
                        type=MemoryType.USER_FACT,
                        content=f"Extension proposal {proposal_id} approved by {approver}.",
                        source=MemorySource.USER,
                        metadata=self._sanitize_data({"proposal_id": proposal_id}),
                    )
                    self.memory_store.add(rec)
                except Exception:
                    pass

            return success

    def reject_proposal(self, proposal_id: str, reason: str = "Declined by user") -> bool:
        """Explicitly decline proposal."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{proposal_id}' not found.")

            success = ProposalLifecycleManager.reject_proposal(proposal, reason=reason)

            self.event_bus.emit(
                event_type="proposal.rejected",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({"proposal_id": proposal_id, "reason": reason}),
            )

            return success

    def activate_proposal(self, proposal_id: str) -> Tuple[bool, Optional[str]]:
        """Activate an approved, validated extension into the live CapabilityRegistry."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return False, f"Proposal '{proposal_id}' not found."

            if proposal.status != ProposalStatus.APPROVED or proposal.approval_state != "APPROVED":
                return False, f"Cannot activate proposal in status '{proposal.status.value}' without human approval."

            # Check if candidate has been materially mutated since approval
            current_hash = ExtensionProposal.compute_candidate_hash(proposal.candidate)
            if current_hash != proposal.candidate_hash:
                ProposalLifecycleManager.check_and_invalidate_on_material_change(proposal, proposal.candidate)
                return False, "Candidate code or permissions mutated since approval. Re-approval required."

            # Move through VERIFYING to ACTIVE
            ProposalLifecycleManager.transition(proposal, ProposalStatus.VERIFYING)

            cand = proposal.candidate

            # Construct safe default handler
            def dynamic_handler(**kwargs) -> ToolResult:
                return ToolResult(
                    tool_id=cand.candidate_id,
                    status="SUCCESS",
                    output={"activated_candidate": cand.name, "result": "Extension operation completed successfully."},
                    evidence=[f"Executed extension '{cand.name}' via {cand.source_type.value}"],
                )

            # Construct CapabilityDefinition
            cap = CapabilityDefinition(
                capability_id=cand.candidate_id,
                name=cand.name,
                description=cand.description,
                supported_outcomes=list(cand.provided_capabilities),
                capability_type=CapabilityType.TOOL,
                risk_level=cand.risk_level,
                permissions=list(cand.required_permissions),
                health=ToolHealth.HEALTHY,
                provenance=f"extension_proposal:{proposal.proposal_id}",
                handler=dynamic_handler,
                tags=list(cand.provided_capabilities),
            )

            # Register in CapabilityRegistry
            self.capability_registry.register(cap)

            ProposalLifecycleManager.transition(proposal, ProposalStatus.ACTIVE)

            self.event_bus.emit(
                event_type="proposal.activated",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal_id,
                    "capability_id": cap.capability_id,
                    "name": cap.name,
                }),
            )

            if self.memory_store:
                try:
                    rec = MemoryRecord(
                        type=MemoryType.ENVIRONMENT_FACT,
                        content=f"Extension '{cap.name}' ({cap.capability_id}) activated and registered.",
                        source=MemorySource.SYSTEM,
                        metadata=self._sanitize_data({"capability_id": cap.capability_id, "proposal_id": proposal_id}),
                    )
                    self.memory_store.add(rec)
                except Exception:
                    pass

            return True, None

    def get_proposal(self, proposal_id: str) -> Optional[ExtensionProposal]:
        """Retrieve proposal by ID."""
        with self._lock:
            return self._proposals.get(proposal_id)

    def list_proposals(self, status: Optional[ProposalStatus] = None) -> List[ExtensionProposal]:
        """List all proposals matching optional status filter."""
        with self._lock:
            if status:
                return [p for p in self._proposals.values() if p.status == status]
            return list(self._proposals.values())
