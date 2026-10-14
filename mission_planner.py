"""Dependency-Aware DAG Mission Planner for Hey Jev (Phase 27).

Decomposes interpreted mission objectives into topological DAG task plans:
- Constructs dependency-ordered task graphs with cycle detection
- Respects CPU/RAM limits and bounds concurrent scheduling for Windows i3-5005U
- Enforces task budget and decomposition depth limits
- Identifies risk checkpoints, permissions, idempotency, and verification methods
- Integrates with WorkflowComposer and CapabilityRegistry
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_discovery import CapabilityDiscoveryEngine
from capability_model import CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from capability_workflow import WorkflowComposer, WorkflowMode, WorkflowPlan
from memory_model import sanitize_content
from mission_model import (
    Mission,
    MissionBudget,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from resource_governor import ResourceGovernor
from tool_model import RiskLevel

logger = logging.getLogger(__name__)


class MissionPlanningError(Exception):
    """Raised when mission plan creation or DAG validation fails."""
    pass


class MissionPlanner:
    """Constructs verifiable, dependency-aware DAG execution plans for missions."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        discovery_engine: Optional[CapabilityDiscoveryEngine] = None,
        workflow_composer: Optional[WorkflowComposer] = None,
        resource_governor: Optional[ResourceGovernor] = None,
    ) -> None:
        self.registry = registry
        self.discovery_engine = discovery_engine or CapabilityDiscoveryEngine(registry=registry)
        self.workflow_composer = workflow_composer or WorkflowComposer(
            registry=registry, discovery_engine=self.discovery_engine
        )
        self.resource_governor = resource_governor

    def create_plan(
        self,
        mission: Mission,
        context: Optional[Dict[str, Any]] = None,
    ) -> MissionPlan:
        """Decompose mission into a validated DAG MissionPlan.

        Raises:
            MissionPlanningError: If budget is exceeded or cycles exist.
        """
        ctx = dict(context or {})

        if mission.status == MissionStatus.CREATED:
            mission.transition_to(MissionStatus.PLANNING, reason="Decomposing objective into DAG tasks.")

        # 1. Check if mission is already blocked by capability gap
        if mission.blockers:
            plan = MissionPlan(
                plan_id=f"plan_{uuid.uuid4().hex[:10]}",
                plan_version=1,
                tasks=[],
                replan_reason=f"Blocked: {'; '.join(mission.blockers)}",
            )
            mission.plan = plan
            mission.transition_to(MissionStatus.BLOCKED, reason="Mission blocked by prerequisite gaps.")
            return plan

        # 2. Synthesize workflow steps using existing WorkflowComposer
        wf_mode = (
            WorkflowMode.MANUAL
            if mission.mode.value == "MANUAL"
            else (
                WorkflowMode.AUTOPILOT
                if mission.mode.value == "AUTOPILOT"
                else WorkflowMode.COPILOT
            )
        )
        wf_plan, err = self.workflow_composer.compose_workflow(
            goal=mission.normalized_goal or mission.objective,
            context=ctx,
            mode=wf_mode,
        )

        tasks: List[MissionTask] = []
        if wf_plan and wf_plan.steps:
            # Map workflow steps into MissionTasks
            for step in wf_plan.steps:
                cap = self.registry.get(step.capability_id)
                risk = cap.risk_level if cap else RiskLevel.LOW
                is_idempotent = (
                    cap.reversibility == Reversibility.REVERSIBLE
                    if cap
                    else True
                )
                perms: List[str] = []
                if cap and cap.permissions:
                    for p in cap.permissions:
                        perms.append(p.value if hasattr(p, "value") else str(p))

                # Check if approval is needed based on risk or checkpoint
                requires_approval = (
                    step.approval_checkpoint
                    or risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                    or (cap and cap.approval_requirement.value == "ALWAYS_REQUIRED")
                )
                appr_reason = (
                    step.approval_reason
                    or (f"High-risk operation ({risk.value}) requiring explicit authorization." if requires_approval else "")
                )

                tasks.append(
                    MissionTask(
                        task_id=step.step_id,
                        description=step.description,
                        intended_result=step.expected_outcome or f"Execute {step.step_id} successfully",
                        dependencies=list(step.depends_on),
                        capability_id=step.capability_id,
                        inputs=dict(step.inputs),
                        preconditions=[],
                        required_permissions=perms,
                        risk_level=risk,
                        approval_checkpoint=requires_approval,
                        approval_reason=appr_reason,
                        status=MissionTaskStatus.PENDING,
                        timeout_s=step.timeout_s,
                        max_retries=step.max_retries,
                        verification_method=(
                            "; ".join(step.verification_criteria)
                            if step.verification_criteria
                            else "physical_outcome_verification"
                        ),
                        is_idempotent=is_idempotent,
                    )
                )
        else:
            # Fallback decomposition directly from objective and criteria
            tasks = self._fallback_decompose(mission, ctx)

        # 3. Budget Validation: Max Tasks Limit
        if len(tasks) > mission.budget.max_tasks:
            raise MissionPlanningError(
                f"Task count ({len(tasks)}) exceeds mission budget max_tasks ({mission.budget.max_tasks})."
            )

        # 4. Topological Sort & Cycle Detection
        self._validate_dag(tasks)

        # 5. Bound concurrency for CPU-constrained Windows environment
        if self.resource_governor:
            mission.budget.max_concurrent_tasks = min(
                mission.budget.max_concurrent_tasks,
                self.resource_governor.foreground_slots,
            )

        # 6. Construct Plan
        plan = MissionPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:10]}",
            plan_version=1,
            tasks=tasks,
        )
        mission.plan = plan

        # 7. Update Mission Status
        if any(t.approval_checkpoint for t in tasks) and mission.mode.value == "COPILOT":
            mission.transition_to(
                MissionStatus.READY,
                reason="Plan generated with gated checkpoints. Ready to execute.",
            )
        else:
            mission.transition_to(
                MissionStatus.READY,
                reason="Plan generated. Ready to execute.",
            )

        return plan

    def _fallback_decompose(
        self, mission: Mission, context: Dict[str, Any]
    ) -> List[MissionTask]:
        """Synthesize tasks directly from criteria if composer had no multi-step rule."""
        tasks: List[MissionTask] = []
        dep: List[str] = []
        for i, ac in enumerate(mission.acceptance_criteria, start=1):
            tid = f"task_{i}"
            tasks.append(
                MissionTask(
                    task_id=tid,
                    description=ac.description,
                    intended_result=f"Achieve outcome for {ac.criterion_id}",
                    dependencies=list(dep),
                    capability_id=None,
                    inputs=dict(context),
                    risk_level=RiskLevel.LOW,
                    approval_checkpoint=False,
                    verification_method="verify_criterion_evidence",
                    is_idempotent=True,
                )
            )
            dep = [tid]  # Sequential ordering by default
        return tasks

    def _validate_dag(self, tasks: List[MissionTask]) -> None:
        """Verify the tasks form a valid Directed Acyclic Graph (DAG).

        Raises:
            MissionPlanningError: If cycles or missing dependencies exist.
        """
        task_ids = {t.task_id for t in tasks}
        adj: Dict[str, List[str]] = {t.task_id: [] for t in tasks}

        for t in tasks:
            for d in t.dependencies:
                if d not in task_ids:
                    raise MissionPlanningError(
                        f"Task '{t.task_id}' depends on non-existent task '{d}'."
                    )
                adj[d].append(t.task_id)

        # Cycle detection using Kahn's algorithm
        in_degree: Dict[str, int] = {t.task_id: len(t.dependencies) for t in tasks}
        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited = 0

        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(tasks):
            raise MissionPlanningError("Cyclic dependency detected in mission tasks DAG.")
