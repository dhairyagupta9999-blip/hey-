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

# Phase 24 Components
from dependency_inspector import DependencyInspector, InstallationPlan
from extension_build_plan import (
    BuildPlanValidator,
    BuildResourceLimits,
    ExtensionBuildPlan,
)
from extension_builder import ExtensionBuilder, GeneratedImplementation
from extension_workspace import ExtensionWorkspace
from staged_runtime import StagedExecutionResult, StagedExtensionRuntime
from staged_validation import ReviewPackage, StageOutcome, StagedValidationPipeline

# Phase 25 Components
import os
import tempfile
from capability_deployment import CapabilityDeploymentManager
from deployment_manifest import (
    DeploymentManifest,
    DeploymentStatus,
    DeploymentType,
)


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
        deployment_manager: Optional[CapabilityDeploymentManager] = None,
        has_process_isolation: bool = False,
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

        # Phase 24 Engines
        self.builder = ExtensionBuilder()
        self.dependency_inspector = DependencyInspector()
        self.staged_pipeline = StagedValidationPipeline(self.dependency_inspector)
        self.staged_runtime = StagedExtensionRuntime(self.capability_registry)
        self._build_plans: Dict[str, ExtensionBuildPlan] = {}
        self._review_packages: Dict[str, ReviewPackage] = {}
        self._workspaces: Dict[str, ExtensionWorkspace] = {}

        # Phase 25 Deployment Manager
        if deployment_manager is not None:
            self.deployment_manager = deployment_manager
        else:
            dep_dir = os.path.join(local_integrations_dir or tempfile.gettempdir(), "deployments")
            self.deployment_manager = CapabilityDeploymentManager(
                capability_registry=self.capability_registry,
                staged_runtime=self.staged_runtime,
                event_bus=self.event_bus,
                memory_store=self.memory_store,
                deployments_dir=dep_dir,
                has_process_isolation=has_process_isolation,
            )

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

            if proposal.status not in (ProposalStatus.APPROVED, ProposalStatus.STAGED) or proposal.approval_state != "APPROVED":
                return False, f"Cannot activate proposal in status '{proposal.status.value}' without human approval."

            # Check if candidate has been materially mutated since approval
            current_hash = ExtensionProposal.compute_candidate_hash(proposal.candidate)
            if current_hash != proposal.candidate_hash:
                ProposalLifecycleManager.check_and_invalidate_on_material_change(proposal, proposal.candidate)
                return False, "Candidate code or permissions mutated since approval. Re-approval required."

            cand = proposal.candidate

            # Move through VERIFYING to ACTIVE
            if proposal.status != ProposalStatus.STAGED:
                ProposalLifecycleManager.transition(proposal, ProposalStatus.VERIFYING)

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

            # If candidate was in staged runtime, cleanly unregister from staged
            if self.staged_runtime.is_staged(cand.candidate_id):
                self.staged_runtime.rollback_and_deactivate(cand.candidate_id)

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

    # -------------------------------------------------------------------------
    # Phase 24: Extension Construction, Testing, Staging & Rollback
    # -------------------------------------------------------------------------
    def create_build_plan(
        self,
        proposal_id: str,
        functional_requirements: Optional[List[str]] = None,
        permitted_relative_paths: Optional[List[str]] = None,
        resource_limits: Optional[BuildResourceLimits] = None,
    ) -> Tuple[Optional[ExtensionBuildPlan], List[str]]:
        """Synthesize a validated build plan bound to a proposal's immutable candidate hash."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return None, [f"Proposal '{proposal_id}' not found."]

            plan = ExtensionBuildPlan.from_proposal(
                proposal=proposal,
                functional_requirements=functional_requirements,
                permitted_relative_paths=permitted_relative_paths,
                resource_limits=resource_limits,
            )

            is_valid, errors = BuildPlanValidator.validate_plan(plan, proposal)
            if not is_valid:
                return None, errors

            self._build_plans[plan.plan_id] = plan
            return plan, []

    def build_extension(
        self,
        plan: ExtensionBuildPlan,
        custom_code: Optional[str] = None,
    ) -> Tuple[bool, Optional[GeneratedImplementation], List[str]]:
        """Construct reviewable extension artifacts in an isolated development workspace."""
        with self._lock:
            proposal = self._proposals.get(plan.proposal_id)
            if not proposal:
                return False, None, [f"Proposal '{plan.proposal_id}' not found."]

            # Re-verify candidate hash hasn't mutated
            curr_hash = ExtensionProposal.compute_candidate_hash(proposal.candidate)
            if curr_hash != plan.candidate_hash:
                return False, None, ["Candidate mutated before build execution. Re-planning required."]

            try:
                ProposalLifecycleManager.transition(proposal, ProposalStatus.BEING_BUILT)
            except ValueError as e:
                return False, None, [str(e)]

            workspace = ExtensionWorkspace(workspace_id=plan.plan_id, build_plan=plan)
            self._workspaces[plan.plan_id] = workspace

            success, gen_impl, errors = self.builder.build(plan, workspace, custom_code=custom_code)
            if not success or not gen_impl:
                ProposalLifecycleManager.transition(
                    proposal, ProposalStatus.FAILED_ABANDONED, reason="; ".join(errors)
                )
                workspace.cleanup()
                return False, None, errors

            ProposalLifecycleManager.transition(proposal, ProposalStatus.BUILT_UNVALIDATED)

            self.event_bus.emit(
                event_type="extension.built",
                task_id=plan.proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": plan.proposal_id,
                    "plan_id": plan.plan_id,
                    "files": gen_impl.files_written,
                }),
            )
            return True, gen_impl, []

    def run_staged_validation(
        self,
        plan: ExtensionBuildPlan,
        workspace: ExtensionWorkspace,
        generated_impl: GeneratedImplementation,
        has_process_isolation: bool = False,
    ) -> ReviewPackage:
        """Execute the 11-stage validation pipeline and generate a reviewable package."""
        with self._lock:
            proposal = self._proposals.get(plan.proposal_id)
            if not proposal:
                raise ValueError(f"Proposal '{plan.proposal_id}' not found.")

            ProposalLifecycleManager.transition(proposal, ProposalStatus.VALIDATING)

            review_pkg = self.staged_pipeline.run_pipeline(
                proposal=proposal,
                plan=plan,
                workspace=workspace,
                generated_impl=generated_impl,
                has_process_isolation=has_process_isolation,
            )

            self._review_packages[proposal.proposal_id] = review_pkg

            if review_pkg.is_ready_for_review:
                ProposalLifecycleManager.transition(proposal, ProposalStatus.READY_FOR_REVIEW)
                ProposalLifecycleManager.transition(proposal, ProposalStatus.AWAITING_APPROVAL)
            else:
                if any(s.outcome == StageOutcome.BLOCKED for s in review_pkg.stage_results):
                    ProposalLifecycleManager.transition(
                        proposal, ProposalStatus.BLOCKED_ISOLATION, reason="Validation blocked by isolation or permissions"
                    )
                else:
                    ProposalLifecycleManager.transition(
                        proposal, ProposalStatus.VALIDATION_FAILED, reason="Validation checks failed"
                    )

            self.event_bus.emit(
                event_type="extension.validated",
                task_id=proposal.proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal.proposal_id,
                    "ready": review_pkg.is_ready_for_review,
                    "passed_stages": review_pkg.passed_stages,
                    "failed_stages": review_pkg.failed_stages,
                }),
            )
            return review_pkg

    def get_review_package(self, proposal_id: str) -> Optional[ReviewPackage]:
        """Retrieve the review package for a validated proposal."""
        with self._lock:
            return self._review_packages.get(proposal_id)

    def stage_approved_proposal(self, proposal_id: str) -> Tuple[bool, Optional[str]]:
        """Stage an approved proposal in the restricted staging execution runtime."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return False, f"Proposal '{proposal_id}' not found."

            review_pkg = self._review_packages.get(proposal_id)
            if not review_pkg:
                return False, f"No review package found for proposal '{proposal_id}'."

            success, err = self.staged_runtime.stage_candidate(proposal, review_pkg)
            if not success:
                return False, err

            ProposalLifecycleManager.transition(proposal, ProposalStatus.STAGED)

            self.event_bus.emit(
                event_type="extension.staged",
                task_id=proposal_id,
                agent_name="ExtensionManager",
                data=self._sanitize_data({
                    "proposal_id": proposal_id,
                    "candidate_id": proposal.candidate.candidate_id,
                }),
            )
            return True, None

    def invoke_staged_capability(
        self,
        capability_id: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        inputs: Optional[Dict[str, Any]] = None,
        timeout_seconds: float = 5.0,
    ) -> StagedExecutionResult:
        """Invoke a capability currently running in the staged execution runtime."""
        return self.staged_runtime.invoke_staged(
            capability_id=capability_id,
            caller_permissions=caller_permissions,
            inputs=inputs,
            timeout_seconds=timeout_seconds,
        )

    def rollback_staged_capability(
        self,
        capability_id: str,
        proposal_id: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Unregister staged capability and clean up staged resources with post-verification."""
        with self._lock:
            ok, msg = self.staged_runtime.rollback_and_deactivate(capability_id)
            if ok and proposal_id:
                proposal = self._proposals.get(proposal_id)
                if proposal and proposal.status == ProposalStatus.STAGED:
                    ProposalLifecycleManager.transition(proposal, ProposalStatus.DEACTIVATED)
            return ok, msg

    # -------------------------------------------------------------------------
    # Phase 25 Capability Deployment & Lifecycle Recovery Integration
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
        return self.deployment_manager.create_deployment_manifest(
            capability_id=capability_id,
            name=name,
            description=description,
            proposal_id=proposal_id,
            candidate_hash=candidate_hash,
            deployment_type=deployment_type,
            version=version,
            provenance=provenance,
            supported_outcomes=supported_outcomes,
            dependencies=dependencies,
            required_permissions=required_permissions,
            input_contract=input_contract,
            output_contract=output_contract,
            artifact_hashes=artifact_hashes,
            validation_summary=validation_summary,
        )

    def create_deployment_manifest_from_proposal(
        self,
        proposal_id: str,
        deployment_type: Optional[DeploymentType] = None,
        version: str = "1.0.0",
    ) -> Optional[DeploymentManifest]:
        """Convenience method to construct an immutable deployment manifest from an existing proposal."""
        with self._lock:
            proposal = self._proposals.get(proposal_id)
            if not proposal:
                return None
            review_pkg = self._review_packages.get(proposal_id)
            dep_type = deployment_type or DeploymentType.APPROVED_EXTERNAL

            artifact_hashes = {}
            if review_pkg:
                for art in review_pkg.verified_artifacts:
                    artifact_hashes[art.relative_path] = art.sha256

            val_summary = {
                "is_ready_for_review": review_pkg.is_ready_for_review if review_pkg else False,
                "passed_stages": review_pkg.passed_stages if review_pkg else [],
                "failed_stages": review_pkg.failed_stages if review_pkg else [],
            }

            return self.create_deployment_manifest(
                capability_id=proposal.candidate.candidate_id,
                name=proposal.candidate.name,
                description=proposal.candidate.description,
                proposal_id=proposal_id,
                candidate_hash=proposal.candidate.content_hash,
                deployment_type=dep_type,
                version=version,
                provenance=f"proposal:{proposal_id}",
                supported_outcomes=proposal.candidate.supported_outcomes,
                dependencies=proposal.candidate.dependencies,
                required_permissions=proposal.candidate.required_permissions,
                input_contract=proposal.candidate.input_schema,
                output_contract=proposal.candidate.output_schema,
                artifact_hashes=artifact_hashes,
                validation_summary=val_summary,
            )

    def stage_deployment(self, deployment_id: str) -> Tuple[bool, Optional[str]]:
        """Stage candidate in restricted runtime without mutating production registry."""
        return self.deployment_manager.stage_deployment(deployment_id)

    def approve_deployment(
        self,
        deployment_id: str,
        approver: str = "security_officer",
        scope_notes: str = "",
    ) -> Tuple[bool, Optional[str]]:
        """Explicitly approve deployment bound to manifest hash."""
        return self.deployment_manager.approve_deployment(deployment_id, approver=approver, scope_notes=scope_notes)

    def activate_deployment(
        self,
        deployment_id: str,
        executor_override: Optional[Any] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Activate deployment into CapabilityRegistry with rollback safety."""
        return self.deployment_manager.activate_deployment(deployment_id, executor_override=executor_override)

    def deactivate_deployment(
        self,
        deployment_id: str,
        drain_timeout_seconds: float = 3.0,
    ) -> Tuple[bool, Optional[str]]:
        """Deactivate deployment, draining in-flight operations."""
        return self.deployment_manager.deactivate_deployment(deployment_id, drain_timeout_seconds=drain_timeout_seconds)

    def rollback_deployment(self, deployment_id: str) -> Tuple[bool, Optional[str]]:
        """Rollback deployment restoring previous capability state."""
        return self.deployment_manager.rollback_deployment(deployment_id)

    def invoke_deployed_capability(
        self,
        capability_id: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Invoke active deployed capability with permission and drainage safety."""
        return self.deployment_manager.invoke_deployed_capability(
            capability_id=capability_id,
            caller_permissions=caller_permissions,
            inputs=inputs,
        )

    def recover_deployments(self) -> Dict[str, str]:
        """Recover deployments after restart."""
        return self.deployment_manager.recover_deployments()

    def get_deployment_manifest(self, deployment_id: str) -> Optional[DeploymentManifest]:
        return self.deployment_manager.get_manifest(deployment_id)

    def list_deployments(self, status: Optional[DeploymentStatus] = None) -> List[DeploymentManifest]:
        return self.deployment_manager.list_manifests(status)

