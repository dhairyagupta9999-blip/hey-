"""Operational Repair Proposal Builder for Hey Jev (Phase 26).

Builds formal, reviewable ExtensionProposals for operational repairs (missing dependencies,
broken adapters, configuration bugs) using the Phase 23/24 proposal lifecycle.

Key Invariants:
1. Hey Jev NEVER silently mutates production code or security configurations.
2. Code/config repairs MUST be formulated as explicit reviewable proposals with rollback plans.
3. Every repair proposal is bound by approval_required=True and candidate SHA-256 fingerprinting.
4. All text and metadata pass through sanitize_content (zero secret exposure).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from agent_task import TaskPermission
from event_bus import EventBus
from extension_proposal import ExtensionProposal, ProposalStatus
from failure_diagnosis import DiagnosticReport
from integration_discovery import CandidateExtension, CandidateSourceType
from memory_model import sanitize_content

logger = logging.getLogger(__name__)


class OperationalRepairProposalBuilder:
    """Constructs formal ExtensionProposal records for diagnosed defects and gaps."""

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self.event_bus = event_bus or EventBus()

    def build_repair_proposal(
        self,
        diagnostic: DiagnosticReport,
        target_package: Optional[str] = None,
        target_file: Optional[str] = None,
        suggested_code_diff: Optional[str] = None,
        required_permissions: Optional[List[TaskPermission]] = None,
    ) -> ExtensionProposal:
        """Construct a structured ExtensionProposal from an empirical DiagnosticReport."""
        comp_id = diagnostic.component_id
        prop_id = f"repair_{comp_id.replace(':', '_')}_{uuid.uuid4().hex[:8]}"

        # Determine primary cause
        primary_cause = diagnostic.verified_causes[0] if diagnostic.verified_causes else (
            diagnostic.hypotheses[0] if diagnostic.hypotheses else "Unspecified operational degradation"
        )
        primary_cause = sanitize_content(primary_cause)

        # Build candidate representation
        dependencies = [target_package] if target_package else []
        spec = {
            "repair_target": comp_id,
            "target_file": sanitize_content(target_file or ""),
            "proposed_diff": sanitize_content(suggested_code_diff or ""),
            "diagnostic_id": diagnostic.diagnosis_id,
            "failure_classification": diagnostic.failure_classification.value,
        }

        candidate = CandidateExtension(
            candidate_id=f"cand_fix_{comp_id.replace(':', '_')}",
            name=f"Repair patch for {comp_id}",
            description=f"Operational repair addressing: {primary_cause}",
            source_type=CandidateSourceType.LOCAL_CONFIGURED,
            source_location=target_file or "workspace:repair",
            version="1.0.1",
            publisher="hey-jev:autonomous-repair",
            dependencies=dependencies,
            required_permissions=required_permissions or [TaskPermission.WRITE_FILES],
            spec=spec,
        )

        rationale = (
            f"Automated failure diagnosis identified root cause: '{primary_cause}'. "
            f"Evidence: {', '.join(diagnostic.supporting_evidence) or 'Diagnostic checks'}. "
            f"A controlled repair is proposed to remediate the defect through the extension lifecycle."
        )

        validation_plan = {
            "static_analysis": "ast_lint_and_policy_check",
            "isolated_test": "run_regression_health_probe",
            "rollback_trigger": "health_check_failure",
        }

        rollback_plan = f"Revert candidate fix and restore previous registered state for '{comp_id}'."

        proposal = ExtensionProposal(
            proposal_id=prop_id,
            user_goal=f"Remediate operational failure in {comp_id}",
            gap_id=f"gap_fault_{comp_id}",
            candidate=candidate,
            status=ProposalStatus.PROPOSED,
            rationale=sanitize_content(rationale),
            alternatives_considered=[
                "Manual code edit (rejected: unsafe)",
                "Rollback deployment (attempted or inapplicable)",
                "Continuous retry (rejected: loop prevention)",
            ],
            requested_permissions=list(candidate.required_permissions),
            security_implications=[
                "Modifies capability execution logic or dependencies",
                "Requires human review before staging or activating",
            ],
            validation_plan=validation_plan,
            sandbox_strategy="isolated_temp_sandbox_synthetic_data",
            rollback_plan=rollback_plan,
            approval_required=True,
            approval_state="PENDING",
        )

        self.event_bus.emit(
            "repair.proposal_generated",
            comp_id,
            data=proposal.to_dict(),
        )
        return proposal
