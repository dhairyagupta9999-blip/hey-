"""Adaptive Replanning Engine for Hey Jev Missions (Phase 27).

Manages dynamic plan revision when environmental circumstances, dependencies,
or capability availability change:
- Records explicit rationale for replanning
- Strictly preserves completed tasks and their verified evidence
- Never repeats completed non-idempotent tasks
- Enforces strict replan budgets and stops safely on exhaustion
- Detects material scope expansion and invalidates stale approvals
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional, Set

from capability_discovery import CapabilityDiscoveryEngine
from capability_model import CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from memory_model import sanitize_content
from mission_model import (
    Mission,
    MissionBudget,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from tool_model import RiskLevel

logger = logging.getLogger(__name__)


class BudgetExhaustedError(Exception):
    """Raised when mission budget limits (replans, tasks, retries) are exhausted."""
    pass


RISK_ORDER = {
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class MissionReplanner:
    """Safely adapts and revises mission plans in response to runtime failures or shifts."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        discovery_engine: Optional[CapabilityDiscoveryEngine] = None,
    ) -> None:
        self.registry = registry
        self.discovery_engine = discovery_engine or CapabilityDiscoveryEngine(registry=registry)

    def replan(
        self,
        mission: Mission,
        trigger_reason: str,
        context: Optional[Dict[str, Any]] = None,
        failed_task_id: Optional[str] = None,
        alternative_capability_id: Optional[str] = None,
    ) -> MissionPlan:
        """Revise the mission plan following a failure, capability loss, or constraint change.

        Raises:
            BudgetExhaustedError: If max_replan_attempts has been reached.
        """
        sanitized_reason = sanitize_content(trigger_reason)

        # 1. Budget Exhaustion Check
        if mission.budget.is_replan_exhausted():
            reason = (
                f"Budget exhausted: max replan attempts ({mission.budget.max_replan_attempts}) "
                f"reached. Cannot replan for: {sanitized_reason}"
            )
            # Safe stop condition: transition to FAILED or NEEDS_ATTENTION
            mission.transition_to(MissionStatus.FAILED, reason=reason)
            raise BudgetExhaustedError(reason)

        mission.budget.current_replan_count += 1
        ctx = dict(context or {})

        old_plan = mission.plan
        if not old_plan:
            raise ValueError("Cannot replan a mission without an existing plan.")

        # 2. Preserve Completed Tasks and Verified Evidence
        preserved_completed: List[MissionTask] = []
        old_completed_ids: Set[str] = set()

        for t in old_plan.tasks:
            if t.status == MissionTaskStatus.COMPLETED:
                preserved_completed.append(t)
                old_completed_ids.add(t.task_id)

        # 3. Construct Revised Task Graph
        revised_tasks: List[MissionTask] = list(preserved_completed)
        materially_changed = False
        new_permissions_needed: List[str] = []

        # Find remaining tasks that were pending or failed
        for t in old_plan.tasks:
            if t.task_id in old_completed_ids:
                continue

            if failed_task_id and t.task_id == failed_task_id:
                # Handle failed task
                if alternative_capability_id:
                    # Alternative capability substitution
                    alt_cap = self.registry.get(alternative_capability_id)
                    alt_risk = alt_cap.risk_level if alt_cap else RiskLevel.LOW
                    alt_perms = (
                        [p.value if hasattr(p, "value") else str(p) for p in alt_cap.permissions]
                        if alt_cap and alt_cap.permissions
                        else []
                    )

                    revised_task = MissionTask(
                        task_id=f"{t.task_id}_alt",
                        description=f"{t.description} (via alternative {alternative_capability_id})",
                        intended_result=t.intended_result,
                        dependencies=[d for d in t.dependencies if d in old_completed_ids or d != failed_task_id],
                        capability_id=alternative_capability_id,
                        inputs=dict(t.inputs),
                        preconditions=list(t.preconditions),
                        required_permissions=alt_perms,
                        risk_level=alt_risk,
                        approval_checkpoint=(
                            alt_risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                            or (alt_cap and alt_cap.approval_requirement.value == "ALWAYS_REQUIRED")
                        ),
                        status=MissionTaskStatus.PENDING,
                        timeout_s=t.timeout_s,
                        max_retries=t.max_retries,
                        verification_method=t.verification_method,
                        is_idempotent=t.is_idempotent,
                    )

                    # Check material change: higher risk or new permissions
                    alt_risk_num = RISK_ORDER.get(alt_risk, 1)
                    orig_risk_num = RISK_ORDER.get(t.risk_level, 1)
                    if alt_risk_num > orig_risk_num or any(p not in mission.required_permissions for p in alt_perms):
                        materially_changed = True
                        new_permissions_needed.extend([p for p in alt_perms if p not in mission.required_permissions])

                    revised_tasks.append(revised_task)
                else:
                    # Capability became unavailable and no substitute
                    revised_task = MissionTask(
                        task_id=t.task_id,
                        description=t.description,
                        intended_result=t.intended_result,
                        dependencies=list(t.dependencies),
                        capability_id=None,
                        inputs=dict(t.inputs),
                        status=MissionTaskStatus.BLOCKED,
                        error=f"Capability unavailable: {sanitized_reason}",
                    )
                    revised_tasks.append(revised_task)
            else:
                # Keep other pending tasks, updating dependency pointers if needed
                revised_task = MissionTask(
                    task_id=t.task_id,
                    description=t.description,
                    intended_result=t.intended_result,
                    dependencies=[
                        f"{d}_alt" if failed_task_id and d == failed_task_id and alternative_capability_id else d
                        for d in t.dependencies
                    ],
                    capability_id=t.capability_id,
                    inputs=dict(t.inputs),
                    preconditions=list(t.preconditions),
                    required_permissions=list(t.required_permissions),
                    risk_level=t.risk_level,
                    approval_checkpoint=t.approval_checkpoint,
                    approval_reason=t.approval_reason,
                    status=MissionTaskStatus.PENDING if t.status == MissionTaskStatus.RUNNING else t.status,
                    retry_count=t.retry_count,
                    max_retries=t.max_retries,
                    timeout_s=t.timeout_s,
                    verification_method=t.verification_method,
                    is_idempotent=t.is_idempotent,
                )
                revised_tasks.append(revised_task)

        # 4. Check for Material Scope Expansion
        # If any newly added tasks introduce higher risk or require new permissions
        if materially_changed:
            # Invalidate stale approvals!
            mission.approved_plan_version = None
            mission.approved_tasks.clear()
            if new_permissions_needed:
                for p in new_permissions_needed:
                    if p not in mission.required_permissions:
                        mission.required_permissions.append(p)

        # 5. Assemble New Plan Version
        new_plan = MissionPlan(
            plan_id=f"plan_{uuid.uuid4().hex[:10]}",
            plan_version=old_plan.plan_version + 1,
            tasks=revised_tasks,
            replan_reason=sanitized_reason,
            materially_changed=materially_changed,
        )
        mission.plan = new_plan

        # 6. Update Mission Status
        has_blocked = any(t.status == MissionTaskStatus.BLOCKED for t in revised_tasks)
        if has_blocked:
            mission.transition_to(
                MissionStatus.BLOCKED,
                reason=f"Plan revised (v{new_plan.plan_version}); tasks blocked by unavailable capabilities.",
            )
        elif materially_changed:
            mission.transition_to(
                MissionStatus.WAITING_FOR_APPROVAL,
                reason=f"Plan revised (v{new_plan.plan_version}) with material scope/risk changes. Prior approvals invalidated.",
            )
        else:
            mission.transition_to(
                MissionStatus.READY,
                reason=f"Plan safely revised (v{new_plan.plan_version}). Completed work preserved.",
            )

        return new_plan
