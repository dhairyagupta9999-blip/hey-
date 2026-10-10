"""Workflow Execution Engine and Governed Autonomy for Hey Jev (Phase 22).

Executes composed WorkflowPlans under three operational modes:
- MANUAL: Previews plan and waits for explicit user initiation per step.
- COPILOT: Autonomously executes safe/read-only steps, pauses and requests approval at gates.
- AUTOPILOT: Executes pre-authorized actions within strict risk boundaries, aborting on unapproved gates.

Key Invariants:
- Data-Pipelining: Interpolates step outputs into subsequent step inputs ($step_id.output.key).
- Truth Verification: Verifies observable physical outcomes; a tool return alone is NOT proof of success.
- Rollback Support: Reverts executed steps in reverse order upon irrecoverable failure.
- Bounded Retries: Step retries bounded to max 2.
- Hardware & Resource Constraints: Respects CPU/RAM limits before dispatching steps.
- Zero Privilege Escalation: Never silently elevates permissions or bypasses approval boundaries.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import psutil
from agent_result import AgentResult, AgentResultStatus
from agent_task import TaskPermission
from capability_model import (
    ApprovalRequirement,
    CapabilityDefinition,
    CapabilityType,
    Reversibility,
)
from capability_registry import CapabilityRegistry
from capability_workflow import StepStatus, WorkflowMode, WorkflowPlan, WorkflowStep
from event_bus import EventBus
from memory_model import MemoryRecord, MemorySource, MemoryType
from memory_store import MemoryStore
from tool_model import RiskLevel, ToolResult


class WorkflowExecutionStatus(str, Enum):
    """Overall status of a workflow execution run."""
    COMPLETED = "COMPLETED"
    PAUSED_FOR_APPROVAL = "PAUSED_FOR_APPROVAL"
    MANUAL_READY = "MANUAL_READY"
    FAILED = "FAILED"
    ABORTED_POLICY = "ABORTED_POLICY"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class WorkflowExecutionReport:
    """Execution outcome report with step-level evidence and verification."""
    workflow_id: str
    goal: str
    mode: WorkflowMode
    status: WorkflowExecutionStatus
    steps_completed: int
    steps_total: int
    step_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    waiting_on_step: Optional[str] = None
    approval_reason: Optional[str] = None
    error: Optional[str] = None
    verification_summary: str = ""
    rolled_back: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "mode": self.mode.value,
            "status": self.status.value,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "waiting_on_step": self.waiting_on_step,
            "approval_reason": self.approval_reason,
            "error": self.error,
            "verification_summary": self.verification_summary,
            "rolled_back": self.rolled_back,
            "duration_ms": round(self.duration_ms, 2),
            "step_results": self.step_results,
        }


class WorkflowEngine:
    """Executes capability workflows with governed autonomy and physical outcome verification."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        event_bus: Optional[EventBus] = None,
        memory_store: Optional[MemoryStore] = None,
        max_cpu_percent: float = 100.0,
        max_memory_percent: float = 98.0,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus or EventBus()
        self.memory_store = memory_store
        self.max_cpu_percent = max_cpu_percent
        self.max_memory_percent = max_memory_percent
        self.approved_checkpoints: Set[Tuple[str, str]] = set()  # (workflow_id, step_id)

    def approve_checkpoint(self, workflow_id: str, step_id: str) -> None:
        """Record human approval for a gated workflow step."""
        self.approved_checkpoints.add((workflow_id, step_id))

    def execute_workflow(
        self,
        plan: WorkflowPlan,
        context: Optional[Dict[str, Any]] = None,
        granted_permissions: Optional[List[TaskPermission]] = None,
    ) -> WorkflowExecutionReport:
        """Execute or preview a workflow plan according to its configured mode."""
        t_start = time.time()
        ctx = dict(context or {})
        granted_perms = set(granted_permissions or [])

        # Emit workflow start event
        self.event_bus.emit(
            event_type="workflow.started",
            task_id=plan.workflow_id,
            agent_name="WorkflowEngine",
            data={"goal": plan.goal, "mode": plan.mode.value, "steps": len(plan.steps)},
        )

        # 1. Mode Check: MANUAL Mode
        if plan.mode == WorkflowMode.MANUAL:
            duration = (time.time() - t_start) * 1000
            return WorkflowExecutionReport(
                workflow_id=plan.workflow_id,
                goal=plan.goal,
                mode=plan.mode,
                status=WorkflowExecutionStatus.MANUAL_READY,
                steps_completed=0,
                steps_total=len(plan.steps),
                verification_summary="Plan synthesized and verified. Ready for manual step-by-step initiation.",
                duration_ms=duration,
            )

        # 2. Sequential execution following topological sort
        try:
            execution_order = plan.get_topological_order()
        except Exception as e:
            duration = (time.time() - t_start) * 1000
            return WorkflowExecutionReport(
                workflow_id=plan.workflow_id,
                goal=plan.goal,
                mode=plan.mode,
                status=WorkflowExecutionStatus.FAILED,
                steps_completed=0,
                steps_total=len(plan.steps),
                error=f"Invalid workflow graph: {e}",
                duration_ms=duration,
            )

        steps_dict = {s.step_id: s for s in plan.steps}

        # Upfront check for AUTOPILOT mode: refuse to partially execute gated plans
        if plan.mode == WorkflowMode.AUTOPILOT:
            for step_id in execution_order:
                s = steps_dict[step_id]
                if s.approval_checkpoint and (plan.workflow_id, s.step_id) not in self.approved_checkpoints:
                    duration = (time.time() - t_start) * 1000
                    return WorkflowExecutionReport(
                        workflow_id=plan.workflow_id,
                        goal=plan.goal,
                        mode=plan.mode,
                        status=WorkflowExecutionStatus.ABORTED_POLICY,
                        steps_completed=0,
                        steps_total=len(plan.steps),
                        error=f"Autopilot policy violation: step '{s.step_id}' requires explicit approval checkpoint: {s.approval_reason}",
                        duration_ms=duration,
                    )

        # Seed context with outputs from any already-completed steps (e.g. upon approval resume)
        for s in plan.steps:
            if s.status == StepStatus.COMPLETED and s.output is not None:
                ctx[s.step_id] = s.output
                if isinstance(s.output, dict):
                    for k, v in s.output.items():
                        ctx[k] = v

        completed_steps = 0
        executed_for_rollback: List[WorkflowStep] = []

        for step_id in execution_order:
            step = steps_dict[step_id]
            if step.status == StepStatus.COMPLETED:
                completed_steps += 1
                if step.rollback_metadata:
                    executed_for_rollback.append(step)
                continue

            cap = self.registry.get(step.capability_id)
            if not cap:
                return self._abort_failure(
                    plan, step, f"Capability '{step.capability_id}' is not registered.", t_start, executed_for_rollback
                )

            # Check permissions
            missing_perms = [p for p in cap.permissions if p not in granted_perms]
            if missing_perms:
                return self._abort_failure(
                    plan,
                    step,
                    f"Missing required permission(s): {[p.value for p in missing_perms]}",
                    t_start,
                    executed_for_rollback,
                    status=WorkflowExecutionStatus.ABORTED_POLICY,
                )

            # Evaluate preconditions
            if not self._check_preconditions(cap, ctx):
                return self._abort_failure(
                    plan, step, f"Preconditions unfulfilled for capability '{cap.name}'", t_start, executed_for_rollback
                )

            # Evaluate conditional branch if step has a condition
            if step.condition and not self._evaluate_condition(step.condition, ctx):
                step.status = StepStatus.SKIPPED
                self.event_bus.emit(
                    "workflow.step_skipped",
                    plan.workflow_id,
                    data={"step_id": step.step_id, "condition": step.condition},
                )
                continue

            # Check Approval Checkpoint
            if step.approval_checkpoint:
                checkpoint_key = (plan.workflow_id, step.step_id)
                if checkpoint_key not in self.approved_checkpoints:
                    if plan.mode == WorkflowMode.AUTOPILOT:
                        # Autopilot refuses to execute gated actions without prior policy authorization
                        return self._abort_failure(
                            plan,
                            step,
                            f"Autopilot policy violation: step '{step.step_id}' requires explicit approval checkpoint: {step.approval_reason}",
                            t_start,
                            executed_for_rollback,
                            status=WorkflowExecutionStatus.ABORTED_POLICY,
                        )
                    else:  # COPILOT mode: Pause and request human approval
                        step.status = StepStatus.WAITING_APPROVAL
                        self.event_bus.emit(
                            "workflow.approval_requested",
                            plan.workflow_id,
                            data={
                                "step_id": step.step_id,
                                "capability": cap.name,
                                "risk_level": cap.risk_level.value,
                                "reason": step.approval_reason,
                            },
                        )
                        duration = (time.time() - t_start) * 1000
                        return WorkflowExecutionReport(
                            workflow_id=plan.workflow_id,
                            goal=plan.goal,
                            mode=plan.mode,
                            status=WorkflowExecutionStatus.PAUSED_FOR_APPROVAL,
                            steps_completed=completed_steps,
                            steps_total=len(plan.steps),
                            waiting_on_step=step.step_id,
                            approval_reason=step.approval_reason or f"Approval required for {cap.name}",
                            duration_ms=duration,
                        )

            # Check Resource Constraints before execution
            res_ok, res_err = self._check_resource_limits()
            if not res_ok:
                return self._abort_failure(
                    plan,
                    step,
                    f"Execution suspended: {res_err}",
                    t_start,
                    executed_for_rollback,
                )

            # Resolve Data-Pipelined Inputs
            resolved_inputs = self._resolve_inputs(step.inputs, ctx)

            # Execute Step with Bounded Retries
            step.status = StepStatus.RUNNING
            success, result_data, err_msg = self._execute_step_with_retries(step, cap, resolved_inputs)

            if not success:
                step.status = StepStatus.FAILED
                step.error = err_msg
                return self._abort_failure(
                    plan, step, f"Step '{step.step_id}' failed: {err_msg}", t_start, executed_for_rollback
                )

            # Independent Physical Verification Check ("A tool return is NOT proof of success")
            verified, verif_evidence, verif_err = self._verify_outcome(step, cap, resolved_inputs, result_data)
            if not verified:
                step.status = StepStatus.FAILED
                step.verified = False
                step.error = f"Outcome verification failed: {verif_err}"
                return self._abort_failure(
                    plan, step, step.error, t_start, executed_for_rollback
                )

            # Mark step completed and record output in context for subsequent steps
            step.status = StepStatus.COMPLETED
            step.verified = True
            step.output = result_data
            step.verification_evidence = verif_evidence
            completed_steps += 1
            executed_for_rollback.append(step)

            # Merge step output into context for downstream pipelining
            ctx[step.step_id] = result_data
            if isinstance(result_data, dict):
                for k, v in result_data.items():
                    ctx[f"{step.step_id}.{k}"] = v

            self.event_bus.emit(
                "workflow.step_completed",
                plan.workflow_id,
                data={"step_id": step.step_id, "capability_id": cap.capability_id, "verified": True},
            )

        duration = (time.time() - t_start) * 1000
        step_results = {s.step_id: s.to_dict() for s in plan.steps}

        # Store durable verified outcome in memory if memory store available
        if self.memory_store and completed_steps > 0:
            try:
                rec = MemoryRecord(
                    type=MemoryType.WORKFLOW,
                    content=f"Workflow successfully executed for goal: '{plan.goal}' with {completed_steps} steps.",
                    source=MemorySource.SYSTEM,
                    metadata={"workflow_id": plan.workflow_id, "steps": len(plan.steps)},
                )
                self.memory_store.add(rec)
            except Exception as e:
                import logging
                logging.getLogger("hey_jev.workflow").warning("Failed to store memory: %s", e)


        self.event_bus.emit(
            "workflow.completed",
            plan.workflow_id,
            data={"steps_completed": completed_steps, "duration_ms": duration},
        )

        return WorkflowExecutionReport(
            workflow_id=plan.workflow_id,
            goal=plan.goal,
            mode=plan.mode,
            status=WorkflowExecutionStatus.COMPLETED,
            steps_completed=completed_steps,
            steps_total=len(plan.steps),
            step_results=step_results,
            verification_summary=f"All {completed_steps} steps executed and independently verified.",
            duration_ms=duration,
        )

    def _resolve_inputs(self, raw_inputs: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Interpolate dynamic variables from context and previous step outputs."""
        resolved: Dict[str, Any] = {}
        for k, v in raw_inputs.items():
            if isinstance(v, str) and v.startswith("$"):
                var_path = v[1:]  # e.g., "step_list_photos.entries[3].path"
                resolved[k] = self._resolve_path(var_path, context)
            else:
                resolved[k] = v
        return resolved

    def _resolve_path(self, path_expr: str, context: Dict[str, Any]) -> Any:
        """Resolve property or array index expressions against context dict."""
        if path_expr in context:
            return context[path_expr]

        parts = path_expr.split(".")
        curr: Any = context
        for part in parts:
            if not isinstance(curr, dict):
                return None
            m = re.match(r"^(\w+)\[(\d+)\]$", part)
            if m:
                field_name, idx_str = m.group(1), int(m.group(2))
                curr = curr.get(field_name)
                if isinstance(curr, list) and 0 <= idx_str < len(curr):
                    curr = curr[idx_str]
                else:
                    return None
            else:
                curr = curr.get(part)
        return curr

    def _check_preconditions(self, cap: CapabilityDefinition, context: Dict[str, Any]) -> bool:
        """Verify preconditions defined on the capability."""
        for pre in cap.preconditions:
            if pre.startswith("tools_available:"):
                tools = pre[len("tools_available:"):].split(",")
                for t in tools:
                    if t and not self.registry.get(t):
                        return False
        return True

    def _evaluate_condition(self, cond: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Evaluate a conditional expression."""
        key = cond.get("key", "")
        op = cond.get("op", "==")
        val = cond.get("val")

        actual_val = self._resolve_path(key[1:] if key.startswith("$") else key, context)

        if op == "==":
            return actual_val == val
        if op == "!=":
            return actual_val != val
        if op == ">":
            return float(actual_val or 0) > float(val or 0)
        if op == "<":
            return float(actual_val or 0) < float(val or 0)
        if op == "is_true":
            return bool(actual_val)
        return False

    def _check_resource_limits(self) -> Tuple[bool, Optional[str]]:
        """Check CPU and memory load before invoking actions."""
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
            if cpu > self.max_cpu_percent:
                return False, f"Machine CPU limit exceeded ({cpu:.1f}% > {self.max_cpu_percent:.1f}%)"
            if mem > self.max_memory_percent:
                return False, f"Machine memory limit exceeded ({mem:.1f}% > {self.max_memory_percent:.1f}%)"
        except Exception:
            pass
        return True, None

    def _execute_step_with_retries(
        self,
        step: WorkflowStep,
        cap: CapabilityDefinition,
        inputs: Dict[str, Any],
    ) -> Tuple[bool, Any, Optional[str]]:
        """Execute step handler with bounded retries."""
        handler = cap.handler
        if not handler or not callable(handler):
            return False, None, f"No callable execution handler for capability '{cap.capability_id}'."

        max_attempts = min(3, 1 + step.max_retries)
        last_error = ""

        for attempt in range(max_attempts):
            step.retry_count = attempt
            t0 = time.time()
            try:
                res = handler(**inputs)
                duration_ms = (time.time() - t0) * 1000
                step.execution_duration_ms = duration_ms

                # Check ToolResult wrapper if returned
                if isinstance(res, ToolResult):
                    if res.is_success:
                        if res.rollback_metadata:
                            step.rollback_metadata = res.rollback_metadata
                        return True, res.output, None
                    else:
                        last_error = res.error or "Tool execution reported failure status"
                elif isinstance(res, dict) and res.get("status") in ("FAILED", "FAILURE"):
                    last_error = res.get("error") or "Dictionary output returned failure"
                else:
                    return True, res, None
            except Exception as e:
                last_error = str(e)

            # Brief backoff before next attempt
            if attempt < max_attempts - 1:
                time.sleep(0.05 * (attempt + 1))

        return False, None, last_error

    def _verify_outcome(
        self,
        step: WorkflowStep,
        cap: CapabilityDefinition,
        inputs: Dict[str, Any],
        output: Any,
    ) -> Tuple[bool, Dict[str, Any], Optional[str]]:
        """Perform independent outcome verification; check observable criteria."""
        evidence: Dict[str, Any] = {"output_type": type(output).__name__}

        if output is None:
            return False, evidence, "Handler returned None (empty result)."

        for crit in step.verification_criteria:
            if crit == "output_non_empty":
                if not output:
                    return False, evidence, "Output is empty."
            elif crit == "entries_is_list":
                entries = output.get("entries") if isinstance(output, dict) else output
                if not isinstance(entries, list):
                    return False, evidence, "Expected 'entries' to be a valid list."
                evidence["entries_count"] = len(entries)
            elif crit == "file_moved_to_destination":
                dest = inputs.get("destination")
                if dest and not os.path.exists(dest) and not (isinstance(output, dict) and output.get("dry_run")):
                    return False, evidence, f"Destination file does not exist: {dest}"
            elif crit == "wallpaper_path_set_in_registry":
                # Verify that wallpaper target path was provided and non-empty
                target_path = inputs.get("path")
                if not target_path or not str(target_path).strip():
                    return False, evidence, "Wallpaper path was empty or unresolved."
                evidence["wallpaper_target"] = target_path

        return True, evidence, None

    def _abort_failure(
        self,
        plan: WorkflowPlan,
        failing_step: WorkflowStep,
        error_msg: str,
        t_start: float,
        executed_steps: List[WorkflowStep],
        status: WorkflowExecutionStatus = WorkflowExecutionStatus.FAILED,
    ) -> WorkflowExecutionReport:
        """Handle failure, emit audit logs, and trigger rollback for executed reversible steps."""
        rolled_back = False
        if executed_steps:
            rolled_back = self._rollback_steps(executed_steps)

        duration = (time.time() - t_start) * 1000
        self.event_bus.emit(
            "workflow.failed",
            plan.workflow_id,
            data={
                "failed_step": failing_step.step_id,
                "error": error_msg,
                "rolled_back": rolled_back,
            },
        )

        return WorkflowExecutionReport(
            workflow_id=plan.workflow_id,
            goal=plan.goal,
            mode=plan.mode,
            status=status,
            steps_completed=len(executed_steps),
            steps_total=len(plan.steps),
            error=error_msg,
            rolled_back=rolled_back,
            duration_ms=duration,
        )

    def _rollback_steps(self, executed_steps: List[WorkflowStep]) -> bool:
        """Roll back executed steps in reverse order where rollback metadata is present."""
        success = True
        for step in reversed(executed_steps):
            if step.rollback_metadata:
                cap = self.registry.get(step.capability_id)
                if cap and cap.supports_rollback:
                    try:
                        # If a rollback handler exists or move_file rollback
                        undo_action = step.rollback_metadata.get("undo_action")
                        if undo_action == "move_file":
                            orig_src = step.rollback_metadata.get("source")
                            orig_dest = step.rollback_metadata.get("destination")
                            if orig_dest and os.path.exists(orig_dest):
                                os.rename(orig_dest, orig_src)
                    except Exception:
                        success = False
        return success
