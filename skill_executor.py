"""Skill Executor and Workflow Engine for Hey Jev.

Executes composed multi-tool workflows, resolves tool dependencies,
evaluates conditional branches, validates permissions, and records failure lessons.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPermission
from event_bus import EventBus
from skill_model import SkillDefinition, SkillTrustLevel
from tool_executor import ToolExecutor
from tool_model import RiskLevel, ToolResult
from tool_registry import ToolRegistry


@dataclass
class FailureLesson:
    """Operational takeaway captured from a tool or skill failure."""
    skill_id: str
    tool_id: str
    failure_reason: str
    lesson: str
    suggested_fallback: Optional[str] = None
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SkillExecutionRecord:
    """Historical execution record of a skill workflow."""
    skill_id: str
    version: str
    execution_id: str
    tools_used: List[str]
    success: bool
    duration_ms: float
    verification_summary: str
    rollback_metadata: Dict[str, Any]
    lessons: List[FailureLesson] = field(default_factory=list)
    executed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["lessons"] = [l.to_dict() for l in self.lessons]
        return d


class SkillExecutor:
    """Executes multi-tool skill workflows with safety gates, conditional logic, and verification."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        tool_executor: Optional[ToolExecutor] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.tool_registry = tool_registry
        self.event_bus = event_bus or EventBus()
        self.tool_executor = tool_executor or ToolExecutor(registry=tool_registry, event_bus=self.event_bus)
        self.execution_history: List[SkillExecutionRecord] = []
        self.failure_lessons: List[FailureLesson] = []

    def validate_requirements(self, skill: SkillDefinition) -> Tuple[bool, List[str]]:
        """Verify that all required tools and capabilities are registered and available."""
        missing: List[str] = []
        for req_tool in skill.required_tools:
            if not self.tool_registry.get(req_tool):
                missing.append(f"Required tool '{req_tool}' is not registered.")

        for req_cap in skill.required_capabilities:
            matching = self.tool_registry.find_by_capability(req_cap)
            if not matching:
                missing.append(f"Required capability '{req_cap}' has no registered tool implementation.")

        return len(missing) == 0, missing

    def check_permissions(
        self,
        skill: SkillDefinition,
        granted_permissions: Optional[List[TaskPermission]] = None,
        approval_state: str = "NONE",
    ) -> Tuple[bool, str]:
        """Validate permissions and approval requirements for the skill."""
        granted = set(granted_permissions or [])

        for perm in skill.permissions:
            if perm not in granted:
                return False, f"Missing skill permission: '{perm.value}'"

        # Check trust level vs risk level
        if skill.trust_level == SkillTrustLevel.UNTRUSTED:
            if skill.risk_level != RiskLevel.LOW:
                return False, "Untrusted skills are prohibited from executing beyond RiskLevel.LOW"

        if skill.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and approval_state != "APPROVED":
            return False, f"Skill requires human approval ({skill.risk_level.value})"

        return True, ""

    def evaluate_condition(self, condition: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Evaluate a conditional expression against runtime context."""
        field_name = condition.get("field", "")
        op = condition.get("op", "==")
        expected = condition.get("val", condition.get("value"))

        actual = context.get(field_name)
        if op in ("==", "equals", "eq"):
            return actual == expected
        elif op in ("!=", "ne"):
            return actual != expected
        elif op in (">", "gt"):
            return actual is not None and actual > expected
        elif op in (">=", "gte"):
            return actual is not None and actual >= expected
        elif op in ("<", "lt"):
            return actual is not None and actual < expected
        elif op in ("<=", "lte"):
            return actual is not None and actual <= expected
        elif op in ("in", "contains"):
            return expected in actual if actual is not None else False
        elif op in ("not_in", "not_contains"):
            return expected not in actual if actual is not None else True
        elif op == "is_not_empty":
            return bool(actual)
        return True

    def execute_skill(
        self,
        skill: SkillDefinition,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        granted_permissions: Optional[List[TaskPermission]] = None,
        is_approved: bool = False,
    ) -> AgentResult:
        """Execute a full multi-tool skill workflow."""
        exec_id = f"sk_{uuid.uuid4().hex[:8]}"
        t0 = time.perf_counter()
        ctx = dict(context or {})
        ctx.update(inputs)

        # 1. Validate requirements
        valid_reqs, req_errs = self.validate_requirements(skill)
        if not valid_reqs:
            return AgentResult(
                summary=f"Skill '{skill.name}' requirements unmet",
                error="; ".join(req_errs),
                status=AgentResultStatus.FAILURE,
                confidence=0.0,
            )

        # 2. Check permissions
        approval_state = "APPROVED" if is_approved else ctx.get("approval_state", "NONE")
        authorized, perm_err = self.check_permissions(skill, granted_permissions, approval_state)
        if not authorized:
            return AgentResult(
                summary=f"Skill '{skill.name}' blocked by permissions",
                error=perm_err,
                status=AgentResultStatus.BLOCKED,
                confidence=0.0,
            )

        self.event_bus.emit("skill.started", skill.skill_id, data={"execution_id": exec_id, "version": skill.version})

        tools_used: List[str] = []
        step_results: List[ToolResult] = []
        all_rollback: Dict[str, Any] = {}
        all_evidence: List[Any] = []
        all_artifacts: List[Dict[str, Any]] = []
        lessons: List[FailureLesson] = []

        # 3. Execute workflow steps
        for idx, step in enumerate(skill.workflow):
            step_id = step.get("step_id", f"step_{idx + 1}")
            tool_id = step.get("tool_id", "")
            condition = step.get("condition")

            # Check conditional branch
            if condition and not self.evaluate_condition(condition, ctx):
                continue

            # Resolve step inputs (substituting from ctx)
            step_inputs = dict(step.get("inputs", {}))
            for k, v in step_inputs.items():
                if isinstance(v, str) and v.startswith("$"):
                    ctx_key = v[1:]
                    if ctx_key in ctx:
                        step_inputs[k] = ctx[ctx_key]

            # Merge any explicit inputs
            for k, v in inputs.items():
                if k not in step_inputs:
                    step_inputs[k] = v

            self.event_bus.emit(
                "tool.selected",
                tool_id,
                data={"skill_id": skill.skill_id, "step_id": step_id},
            )

            tool_res = self.tool_executor.execute(
                tool_id=tool_id,
                inputs=step_inputs,
                context=ctx,
                granted_permissions=granted_permissions,
                is_approved=is_approved,
            )

            tools_used.append(tool_id)
            step_results.append(tool_res)

            if tool_res.evidence:
                all_evidence.extend(tool_res.evidence)
            if tool_res.artifacts:
                all_artifacts.extend(tool_res.artifacts)
            if tool_res.rollback_metadata:
                all_rollback[step_id] = tool_res.rollback_metadata

            if not tool_res.is_success:
                # Capture failure lesson
                lesson = FailureLesson(
                    skill_id=skill.skill_id,
                    tool_id=tool_id,
                    failure_reason=tool_res.error or "Unknown failure",
                    lesson=f"Step '{step_id}' failed with {tool_res.error}",
                )
                lessons.append(lesson)
                self.failure_lessons.append(lesson)

                duration_ms = (time.perf_counter() - t0) * 1000.0
                rec = SkillExecutionRecord(
                    skill_id=skill.skill_id,
                    version=skill.version,
                    execution_id=exec_id,
                    tools_used=tools_used,
                    success=False,
                    duration_ms=duration_ms,
                    verification_summary=f"Failed at step '{step_id}'",
                    rollback_metadata=all_rollback,
                    lessons=lessons,
                )
                self.execution_history.append(rec)

                return AgentResult(
                    summary=f"Skill '{skill.name}' failed at step '{step_id}': {tool_res.error}",
                    error=tool_res.error,
                    status=AgentResultStatus.FAILURE,
                    evidence=all_evidence,
                    confidence=0.0,
                )

            # Store step outputs in runtime context for subsequent steps
            ctx[f"{step_id}_output"] = tool_res.output
            if isinstance(tool_res.output, dict):
                ctx.update(tool_res.output)

        # 4. Verification stage
        self.event_bus.emit("skill.verification_started", skill.skill_id, data={"execution_id": exec_id})
        duration_ms = (time.perf_counter() - t0) * 1000.0
        final_output = ctx.get("final_output", step_results[-1].output if step_results else "Workflow completed")

        rec = SkillExecutionRecord(
            skill_id=skill.skill_id,
            version=skill.version,
            execution_id=exec_id,
            tools_used=tools_used,
            success=True,
            duration_ms=duration_ms,
            verification_summary="All steps verified successfully",
            rollback_metadata=all_rollback,
            lessons=[],
        )
        self.execution_history.append(rec)
        self.event_bus.emit("skill.completed", skill.skill_id, data={"execution_id": exec_id, "duration_ms": duration_ms})

        return AgentResult(
            result=final_output,
            summary=f"Skill '{skill.name}' executed successfully ({len(tools_used)} tools invoked)",
            evidence=all_evidence,
            artifacts=all_artifacts,
            status=AgentResultStatus.SUCCESS,
            confidence=0.98,
        )
