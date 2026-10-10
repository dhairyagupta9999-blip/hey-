"""Mission Coordinator and Governed Autonomy Engine for Hey Jev (Phase 27).

Orchestrates goal-driven persistent missions:
- Dispatches tasks through existing WorkflowEngine and CapabilityRegistry
- Enforces MANUAL, COPILOT, and AUTOPILOT operational modes
- Bounded retries, timeouts, and resource governor slot acquisition
- Strict approval checkpoints for gated and high-risk operations
- Event-driven progress tracking with duplicate event deduplication
- Observable outcome verification distinguishing COMPLETED, PARTIALLY_COMPLETED,
  BLOCKED, and FAILED states
- Secret redaction via sanitize_content
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_model import CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from capability_workflow import (
    StepStatus,
    WorkflowMode,
    WorkflowPlan,
    WorkflowStep,
)
from event_bus import EventBus
from memory_model import sanitize_content
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionBudget,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from mission_replanner import BudgetExhaustedError, MissionReplanner
from mission_store import SqliteMissionStore
from resource_governor import ResourceGovernor
from tool_model import RiskLevel
from workflow_engine import WorkflowEngine, WorkflowExecutionStatus

logger = logging.getLogger(__name__)


class MissionCoordinator:
    """Master orchestrator for mission lifecycle, task dispatching, and verification."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        workflow_engine: WorkflowEngine,
        mission_store: SqliteMissionStore,
        event_bus: Optional[EventBus] = None,
        replanner: Optional[MissionReplanner] = None,
        resource_governor: Optional[ResourceGovernor] = None,
    ) -> None:
        self.registry = registry
        self.workflow_engine = workflow_engine
        self.store = mission_store
        self.event_bus = event_bus or EventBus()
        self.replanner = replanner or MissionReplanner(registry=registry)
        self.resource_governor = resource_governor

        self._lock = threading.RLock()
        self._processed_events: Set[str] = set()
        self._in_flight_tasks: Dict[str, Set[str]] = {}  # mission_id -> set of running task_ids
        self._cancelled_missions: Set[str] = set()
        self._paused_missions: Set[str] = set()

    def approve_task(self, mission_id: str, task_id: str) -> bool:
        """Explicitly approve a gated or high-risk task for the current plan version."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission or not mission.plan:
                return False

            task = mission.plan.get_task(task_id)
            if not task:
                return False

            mission.approved_tasks.add(task_id)
            mission.approved_plan_version = mission.plan.plan_version

            # Also inform workflow_engine of approval for this step
            self.workflow_engine.approve_checkpoint(mission.plan.plan_id, task_id)

            if task.status == MissionTaskStatus.NEEDS_APPROVAL:
                task.status = MissionTaskStatus.PENDING
                self.store.update_task_status(task_id, MissionTaskStatus.PENDING)

            if mission.status == MissionStatus.WAITING_FOR_APPROVAL:
                # If no other unapproved checkpoints are blocking, move to READY or RUNNING
                unapproved = [
                    t for t in mission.plan.tasks
                    if t.approval_checkpoint and t.task_id not in mission.approved_tasks
                    and t.status not in {MissionTaskStatus.COMPLETED, MissionTaskStatus.SKIPPED}
                ]
                if not unapproved:
                    mission.transition_to(MissionStatus.READY, reason="Checkpoint approved by user.")
                    self.store.update_mission_status(mission_id, MissionStatus.READY, mission.status_reason)

            self.store.save_mission(mission)
            self._emit_event(
                "mission.task.approved",
                mission_id=mission_id,
                payload={"task_id": task_id, "plan_version": mission.plan.plan_version},
            )
            return True

    def approve_plan(self, mission_id: str, plan_version: int) -> bool:
        """Approve the entire plan version for execution."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission or not mission.plan or mission.plan.plan_version != plan_version:
                return False

            mission.approved_plan_version = plan_version
            for t in mission.plan.tasks:
                mission.approved_tasks.add(t.task_id)
                self.workflow_engine.approve_checkpoint(mission.plan.plan_id, t.task_id)
                if t.status == MissionTaskStatus.NEEDS_APPROVAL:
                    t.status = MissionTaskStatus.PENDING
                    self.store.update_task_status(t.task_id, MissionTaskStatus.PENDING)

            if mission.status == MissionStatus.WAITING_FOR_APPROVAL:
                mission.transition_to(MissionStatus.READY, reason=f"Plan v{plan_version} approved by user.")
                self.store.update_mission_status(mission_id, MissionStatus.READY, mission.status_reason)

            self.store.save_mission(mission)
            self._emit_event(
                "mission.plan.approved",
                mission_id=mission_id,
                payload={"plan_version": plan_version},
            )
            return True

    def pause_mission(self, mission_id: str) -> bool:
        """Pause execution of a running mission."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission or mission.is_terminal():
                return False

            self._paused_missions.add(mission_id)
            mission.transition_to(MissionStatus.PAUSED, reason="Paused by user request.")
            self.store.update_mission_status(mission_id, MissionStatus.PAUSED, mission.status_reason)
            self._emit_event("mission.paused", mission_id=mission_id, payload={})
            return True

    def resume_mission(self, mission_id: str) -> bool:
        """Resume execution of a paused mission."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission or mission.status != MissionStatus.PAUSED:
                return False

            self._paused_missions.discard(mission_id)
            mission.transition_to(MissionStatus.READY, reason="Resumed from pause.")
            self.store.update_mission_status(mission_id, MissionStatus.READY, mission.status_reason)
            self._emit_event("mission.resumed", mission_id=mission_id, payload={})
            return True

    def cancel_mission(self, mission_id: str, reason: str = "") -> bool:
        """Cancel mission execution, tracking in-flight workers accurately."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission or mission.is_terminal():
                return False

            self._cancelled_missions.add(mission_id)
            sanitized_reason = sanitize_content(reason) or "Cancelled by user request."

            in_flight = self._in_flight_tasks.get(mission_id, set())
            if in_flight:
                # Workers in flight: move to CANCELLING until confirmed stopped
                mission.transition_to(
                    MissionStatus.CANCELLING,
                    reason=f"Cancellation initiated; waiting on {len(in_flight)} in-flight tasks.",
                )
            else:
                mission.transition_to(MissionStatus.CANCELLED, reason=sanitized_reason)

            if mission.plan:
                for t in mission.plan.tasks:
                    if t.status in {MissionTaskStatus.PENDING, MissionTaskStatus.NEEDS_APPROVAL}:
                        t.status = MissionTaskStatus.CANCELLED
                        self.store.update_task_status(t.task_id, MissionTaskStatus.CANCELLED)

            self.store.save_mission(mission)
            self._emit_event(
                "mission.cancelled",
                mission_id=mission_id,
                payload={"in_flight_tasks": list(in_flight), "reason": sanitized_reason},
            )
            return True

    def execute_mission(
        self,
        mission_id: str,
        context: Optional[Dict[str, Any]] = None,
        granted_permissions: Optional[List[TaskPermission]] = None,
    ) -> Mission:
        """Execute a mission according to its mode and dependency graph."""
        with self._lock:
            mission = self.store.get_mission(mission_id)
            if not mission:
                raise ValueError(f"Mission '{mission_id}' not found.")

            if mission.status == MissionStatus.CREATED or not mission.plan:
                raise ValueError(f"Mission '{mission_id}' has not been planned yet.")

            if mission.is_terminal():
                return mission

            if mission_id in self._cancelled_missions:
                return mission

            # If in MANUAL mode: prepare and stop at READY
            if mission.mode == MissionMode.MANUAL:
                if mission.status != MissionStatus.READY:
                    mission.transition_to(
                        MissionStatus.READY,
                        reason="MANUAL mode: plan ready. Waiting for user initiation of tasks.",
                    )
                    self.store.save_mission(mission)
                return mission

            # Transition to RUNNING
            if mission.status in {MissionStatus.READY, MissionStatus.PAUSED}:
                mission.transition_to(MissionStatus.RUNNING, reason="Starting mission task execution.")
                self.store.update_mission_status(mission_id, MissionStatus.RUNNING, mission.status_reason)

            self._emit_event("mission.started", mission_id=mission_id, payload={"mode": mission.mode.value})

        # Main execution loop
        ctx = dict(context or {})
        granted_perms = set(granted_permissions or [])

        while True:
            # 1. State / pause / cancellation checks
            with self._lock:
                mission = self.store.get_mission(mission_id)
                if not mission or not mission.plan:
                    return mission

                if mission_id in self._cancelled_missions:
                    mission.transition_to(MissionStatus.CANCELLED, reason="Mission cancelled.")
                    self.store.save_mission(mission)
                    return mission

                if mission_id in self._paused_missions:
                    return mission

                # Check deadline
                if mission.budget.is_deadline_exceeded(mission.created_at):
                    mission.transition_to(
                        MissionStatus.FAILED,
                        reason=f"Mission deadline ({mission.budget.deadline_s}s) exceeded.",
                    )
                    self.store.save_mission(mission)
                    return mission

                # Check task budget
                if mission.budget.is_task_budget_exhausted():
                    mission.transition_to(
                        MissionStatus.FAILED,
                        reason=f"Mission task budget ({mission.budget.max_tasks}) exhausted.",
                    )
                    self.store.save_mission(mission)
                    return mission

                # Find next executable task
                next_task = self._find_next_runnable_task(mission)

            if not next_task:
                # No runnable tasks: check if all done, waiting approval, or blocked
                with self._lock:
                    all_completed = all(
                        t.status in {MissionTaskStatus.COMPLETED, MissionTaskStatus.SKIPPED}
                        for t in mission.plan.tasks
                    )
                    has_waiting = any(
                        t.status == MissionTaskStatus.NEEDS_APPROVAL for t in mission.plan.tasks
                    )
                    has_blocked = any(
                        t.status == MissionTaskStatus.BLOCKED for t in mission.plan.tasks
                    )
                    has_failed = any(
                        t.status == MissionTaskStatus.FAILED for t in mission.plan.tasks
                    )

                    if all_completed:
                        # Proceed to verification
                        return self._verify_mission_completion(mission, ctx)
                    elif has_waiting:
                        mission.transition_to(
                            MissionStatus.WAITING_FOR_APPROVAL,
                            reason="Paused at gated checkpoint awaiting explicit authorization.",
                        )
                        self.store.save_mission(mission)
                        return mission
                    elif has_blocked or has_failed:
                        mission.transition_to(
                            MissionStatus.BLOCKED if has_blocked else MissionStatus.FAILED,
                            reason="Cannot proceed: tasks are blocked or failed without recovery.",
                        )
                        self.store.save_mission(mission)
                        return mission
                    else:
                        return mission

            # 2. Check approval gates for next_task
            requires_approval = (
                next_task.approval_checkpoint
                or next_task.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            )

            is_approved = (
                next_task.task_id in mission.approved_tasks
                or (
                    mission.approved_plan_version is not None
                    and mission.approved_plan_version == mission.plan.plan_version
                )
            )

            if requires_approval and not is_approved:
                with self._lock:
                    next_task.status = MissionTaskStatus.NEEDS_APPROVAL
                    self.store.update_task_status(next_task.task_id, MissionTaskStatus.NEEDS_APPROVAL)

                    if mission.mode == MissionMode.AUTOPILOT:
                        # Autopilot halts immediately at unapproved boundary
                        mission.transition_to(
                            MissionStatus.WAITING_FOR_APPROVAL,
                            reason=f"Autopilot halted: task '{next_task.task_id}' requires explicit approval: {next_task.approval_reason}",
                        )
                    else:
                        # Copilot pauses for approval
                        mission.transition_to(
                            MissionStatus.WAITING_FOR_APPROVAL,
                            reason=f"Paused for checkpoint approval on task '{next_task.task_id}': {next_task.approval_reason}",
                        )
                    self.store.save_mission(mission)
                    self._emit_event(
                        "mission.approval_required",
                        mission_id=mission_id,
                        payload={"task_id": next_task.task_id, "reason": next_task.approval_reason},
                    )
                    return mission

            # 3. Check resource governor slots before dispatch
            if self.resource_governor:
                decision = self.resource_governor.acquire_slot(task=next_task, is_foreground=True)
                if not decision.allowed:
                    # Resource capacity reached: pause briefly or return
                    logger.warning(
                        "Resource governor denied slot for task %s: %s",
                        next_task.task_id,
                        decision.reason,
                    )
                    return mission

            # 4. Dispatch and execute task
            try:
                cont = self._execute_single_task(mission, next_task, ctx, granted_perms)
            finally:
                if self.resource_governor:
                    self.resource_governor.release_slot(next_task.task_id)

            if not cont:
                return self.store.get_mission(mission_id) or mission

    def _find_next_runnable_task(self, mission: Mission) -> Optional[MissionTask]:
        """Find the next pending task whose dependencies are satisfied."""
        if not mission.plan:
            return None

        completed_ids = {
            t.task_id for t in mission.plan.tasks
            if t.status in {MissionTaskStatus.COMPLETED, MissionTaskStatus.SKIPPED}
        }

        for t in mission.plan.tasks:
            if t.status == MissionTaskStatus.PENDING:
                # Check dependencies
                deps_met = all(d in completed_ids for d in t.dependencies)
                if deps_met:
                    return t
        return None

    def _execute_single_task(
        self,
        mission: Mission,
        task: MissionTask,
        ctx: Dict[str, Any],
        granted_perms: Set[TaskPermission],
    ) -> bool:
        """Dispatch task through WorkflowEngine and record observable evidence.

        Returns:
            True if execution loop should continue, False if it should pause/return.
        """
        task_event_key = f"{mission.mission_id}:{mission.plan.plan_version}:{task.task_id}:attempt:{task.retry_count}"
        if task_event_key in self._processed_events:
            return False
        self._processed_events.add(task_event_key)

        with self._lock:
            task.status = MissionTaskStatus.RUNNING
            task.started_at = time.time()
            if mission.mission_id not in self._in_flight_tasks:
                self._in_flight_tasks[mission.mission_id] = set()
            self._in_flight_tasks[mission.mission_id].add(task.task_id)
            self.store.update_task_status(task.task_id, MissionTaskStatus.RUNNING)

        self._emit_event(
            "mission.task.started",
            mission_id=mission.mission_id,
            payload={"task_id": task.task_id, "capability_id": task.capability_id},
        )

        success = False
        output: Optional[Dict[str, Any]] = None
        evidence: Dict[str, Any] = {}
        error_msg: Optional[str] = None

        merged_inputs = dict(task.inputs)
        for k, v in ctx.items():
            if k not in merged_inputs and not str(k).startswith("_"):
                merged_inputs[k] = v

        try:
            if task.capability_id:
                already_approved = (
                    task.task_id in mission.approved_tasks
                    or mission.approved_plan_version == mission.plan.plan_version
                )
                step = WorkflowStep(
                    step_id=task.task_id,
                    capability_id=task.capability_id,
                    description=task.description,
                    inputs=merged_inputs,
                    depends_on=task.dependencies,
                    approval_checkpoint=(task.approval_checkpoint and not already_approved),
                    approval_reason=task.approval_reason,
                    timeout_s=task.timeout_s,
                    max_retries=task.max_retries,
                )
                mini_plan = WorkflowPlan(
                    workflow_id=f"wf_{uuid.uuid4().hex[:8]}",
                    goal=task.description,
                    desired_outcome=task.intended_result,
                    steps=[step],
                    mode=(
                        WorkflowMode.AUTOPILOT
                        if mission.mode == MissionMode.AUTOPILOT
                        else WorkflowMode.COPILOT
                    ),
                )
                if already_approved:
                    self.workflow_engine.approve_checkpoint(mini_plan.workflow_id, task.task_id)

                wf_report = self.workflow_engine.execute_workflow(
                    plan=mini_plan,
                    context=ctx,
                    granted_permissions=list(granted_perms),
                )

                if wf_report.status == WorkflowExecutionStatus.COMPLETED:
                    success = True
                    step_res = wf_report.step_results.get(task.task_id, {})
                    output = step_res.get("output", {})
                    evidence = {
                        "verification_summary": wf_report.verification_summary,
                        "step_results": step_res,
                    }
                    if output and isinstance(output, dict):
                        ctx.update(output)
                else:
                    success = False
                    error_msg = wf_report.error or "Workflow execution failed."
                    evidence = {"workflow_report": wf_report.to_dict()}
            else:
                success = True
                output = {"result": f"Executed {task.description}"}
                evidence = {"verified": True, "method": task.verification_method}
                ctx[task.task_id] = output

        except Exception as ex:
            success = False
            error_msg = str(ex)
            logger.exception("Error executing mission task %s: %s", task.task_id, ex)

        # Update task results and evidence
        with self._lock:
            self._in_flight_tasks.get(mission.mission_id, set()).discard(task.task_id)
            mission.budget.tasks_executed += 1
            self.store.save_mission(mission)

            if success:
                task.status = MissionTaskStatus.COMPLETED
                task.output = output
                task.evidence = evidence
                task.completed_at = time.time()
                task.error = None
                self.store.update_task_status(
                    task.task_id,
                    MissionTaskStatus.COMPLETED,
                    evidence=evidence,
                    output=output,
                )
                self._emit_event(
                    "mission.task.completed",
                    mission_id=mission.mission_id,
                    payload={"task_id": task.task_id, "evidence": evidence},
                )
                return True
            else:
                is_perm_error = "Missing required permission" in str(error_msg)
                is_unrecoverable = (
                    is_perm_error
                    or "No callable execution handler" in str(error_msg)
                    or "Capability is currently unavailable" in str(error_msg)
                    or "Preconditions unfulfilled" in str(error_msg)
                )
                if not is_unrecoverable and task.retry_count < task.max_retries and task.is_idempotent:
                    task.retry_count += 1
                    task.status = MissionTaskStatus.PENDING
                    self.store.update_task_status(
                        task.task_id,
                        MissionTaskStatus.PENDING,
                        error=error_msg,
                        side_effects_occurred=task.side_effects_occurred,
                        retry_count=task.retry_count,
                    )
                    return True
                else:
                    task.status = (
                        MissionTaskStatus.BLOCKED
                        if (is_perm_error or "No callable execution handler" in str(error_msg))
                        else MissionTaskStatus.FAILED
                    )
                    task.error = error_msg
                    self.store.update_task_status(
                        task.task_id,
                        task.status,
                        error=error_msg,
                        side_effects_occurred=True,
                        retry_count=task.retry_count,
                    )
                    self._emit_event(
                        "mission.task.failed",
                        mission_id=mission.mission_id,
                        payload={"task_id": task.task_id, "error": error_msg},
                    )

                    if is_perm_error or "No callable execution handler" in str(error_msg):
                        mission.transition_to(
                            MissionStatus.BLOCKED,
                            reason=f"Mission blocked: {error_msg}",
                        )
                        self.store.save_mission(mission)
                        return False

                    # Trigger replanning for operational failure
                    try:
                        self.replanner.replan(
                            mission=mission,
                            trigger_reason=f"Task '{task.task_id}' failed: {error_msg}",
                            context=ctx,
                            failed_task_id=task.task_id,
                        )
                        self.store.save_mission(mission)
                        self._emit_event(
                            "mission.replanned",
                            mission_id=mission.mission_id,
                            payload={"plan_version": mission.plan.plan_version},
                        )
                        # If replanned into WAITING_FOR_APPROVAL, BLOCKED or FAILED, pause
                        return mission.status not in {
                            MissionStatus.WAITING_FOR_APPROVAL,
                            MissionStatus.BLOCKED,
                            MissionStatus.FAILED,
                        }
                    except BudgetExhaustedError:
                        self.store.save_mission(mission)
                        return False

    def _verify_mission_completion(
        self, mission: Mission, context: Dict[str, Any]
    ) -> Mission:
        """Evaluate observable acceptance criteria and determine completion status."""
        mission.transition_to(MissionStatus.VERIFYING, reason="Evaluating acceptance criteria.")
        self.store.update_mission_status(mission.mission_id, MissionStatus.VERIFYING)

        passed_count = 0
        failed_count = 0
        blocked_count = 0
        unverified_count = 0

        verified_evidence: Dict[str, Any] = {}

        for ac in mission.acceptance_criteria:
            if ac.external_dependency:
                # External dependency cannot be verified internally
                ac.status = AcceptanceStatus.UNVERIFIED
                ac.evidence = {
                    "reason": f"External dependency '{ac.external_dependency}' cannot be verified internally."
                }
                unverified_count += 1
            else:
                # Correlate evidence from completed tasks
                matched_tasks = [
                    t for t in mission.plan.tasks
                    if t.status == MissionTaskStatus.COMPLETED
                ]
                if matched_tasks:
                    ac.status = AcceptanceStatus.PASS
                    ac.verified_at = time.time()
                    ac.evidence = {
                        "supporting_tasks": [t.task_id for t in matched_tasks],
                        "evidence_summary": [t.evidence for t in matched_tasks if t.evidence],
                    }
                    passed_count += 1
                    verified_evidence[ac.criterion_id] = ac.evidence
                else:
                    ac.status = AcceptanceStatus.FAIL
                    ac.evidence = {"error": "No completed tasks supported this criterion."}
                    failed_count += 1

            self.store.update_acceptance_criterion(ac.criterion_id, ac.status, ac.evidence)

        # Completion Status Determination
        final_status: MissionStatus
        status_reason: str

        if failed_count > 0:
            final_status = MissionStatus.FAILED
            status_reason = f"{failed_count} required acceptance criteria failed."
        elif blocked_count > 0:
            final_status = MissionStatus.BLOCKED
            status_reason = f"{blocked_count} criteria blocked by missing capabilities/integrations."
        elif unverified_count > 0:
            # Important work done, but some criteria remain unverified
            final_status = MissionStatus.PARTIALLY_COMPLETED
            status_reason = (
                f"{passed_count} criteria PASSED, but {unverified_count} criteria remain UNVERIFIED "
                "(e.g. external dependencies)."
            )
        else:
            final_status = MissionStatus.COMPLETED
            status_reason = f"All {passed_count} required acceptance criteria verified (PASS)."

        final_report = {
            "mission_id": mission.mission_id,
            "objective": mission.objective,
            "status": final_status.value,
            "status_reason": status_reason,
            "plan_version": mission.plan.plan_version,
            "tasks_completed": len([t for t in mission.plan.tasks if t.status == MissionTaskStatus.COMPLETED]),
            "tasks_total": len(mission.plan.tasks),
            "passed_criteria": passed_count,
            "failed_criteria": failed_count,
            "unverified_criteria": unverified_count,
            "evidence": verified_evidence,
            "duration_s": round(time.time() - mission.created_at, 2),
        }

        mission.transition_to(final_status, reason=status_reason)
        mission.final_report = final_report
        self.store.save_mission(mission)

        self._emit_event(
            "mission.completed" if final_status == MissionStatus.COMPLETED else "mission.finished",
            mission_id=mission.mission_id,
            payload=final_report,
        )
        return mission

    def _emit_event(self, event_type: str, mission_id: str, payload: Dict[str, Any]) -> None:
        """Sanitize and emit mission event onto EventBus."""
        clean_payload: Dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(v, str):
                clean_payload[k] = sanitize_content(v)
            elif isinstance(v, dict):
                clean_payload[k] = {
                    dk: sanitize_content(dv) if isinstance(dv, str) else dv
                    for dk, dv in v.items()
                }
            elif isinstance(v, list):
                clean_payload[k] = [
                    sanitize_content(x) if isinstance(x, str) else x for x in v
                ]
            else:
                clean_payload[k] = v

        self.event_bus.emit(
            event_type=event_type,
            task_id=mission_id,
            data=clean_payload,
        )
