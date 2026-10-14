"""Persistent Mission Model and Lifecycle for Hey Jev (Phase 27).

Defines core data models, lifecycle states, acceptance criteria, budgets,
and deterministic state transitions for goal-driven autonomous missions.

Enforces:
- Deterministic state machine validation (rejects illegal jumps)
- Explicit acceptance criteria status (PASS, FAIL, BLOCKED, UNVERIFIED)
- Human-in-the-loop operational modes (MANUAL, COPILOT, AUTOPILOT)
- Bounded budgets (task count, replanning count, retries, concurrency, deadlines)
- Strict secret sanitization via sanitize_content
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from memory_model import sanitize_content
from tool_model import RiskLevel


class MissionMode(str, Enum):
    """Operational mode governing autonomy, approvals, and human interaction."""
    MANUAL = "MANUAL"        # Prepares plan, requires user initiation for operational actions
    COPILOT = "COPILOT"      # Automatic execution of safe/read-only steps, pauses at checkpoints
    AUTOPILOT = "AUTOPILOT"  # Policy-governed execution, halts and escalates at risk/boundaries


class MissionStatus(str, Enum):
    """Lifecycle states for a persistent mission."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    BLOCKED = "BLOCKED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PAUSED = "PAUSED"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"


class MissionTaskStatus(str, Enum):
    """Lifecycle states for an individual mission task."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class AcceptanceStatus(str, Enum):
    """Observable evaluation status of an acceptance criterion."""
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


VALID_MISSION_TRANSITIONS: Dict[MissionStatus, Set[MissionStatus]] = {
    MissionStatus.CREATED: {
        MissionStatus.PLANNING,
        MissionStatus.READY,
        MissionStatus.CANCELLED,
    },
    MissionStatus.PLANNING: {
        MissionStatus.READY,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
        MissionStatus.BLOCKED,
        MissionStatus.NEEDS_ATTENTION,
    },
    MissionStatus.READY: {
        MissionStatus.RUNNING,
        MissionStatus.WAITING_FOR_APPROVAL,
        MissionStatus.PAUSED,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.PLANNING,
        MissionStatus.FAILED,
        MissionStatus.BLOCKED,
    },
    MissionStatus.RUNNING: {
        MissionStatus.READY,
        MissionStatus.WAITING_FOR_APPROVAL,
        MissionStatus.BLOCKED,
        MissionStatus.VERIFYING,
        MissionStatus.PAUSED,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.FAILED,
        MissionStatus.NEEDS_ATTENTION,
        MissionStatus.PLANNING,
    },
    MissionStatus.WAITING_FOR_APPROVAL: {
        MissionStatus.RUNNING,
        MissionStatus.READY,
        MissionStatus.BLOCKED,
        MissionStatus.PAUSED,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.FAILED,
        MissionStatus.PLANNING,
    },
    MissionStatus.BLOCKED: {
        MissionStatus.PLANNING,
        MissionStatus.READY,
        MissionStatus.RUNNING,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.FAILED,
        MissionStatus.NEEDS_ATTENTION,
    },
    MissionStatus.VERIFYING: {
        MissionStatus.COMPLETED,
        MissionStatus.PARTIALLY_COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.PLANNING,
        MissionStatus.NEEDS_ATTENTION,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
    },
    MissionStatus.PAUSED: {
        MissionStatus.READY,
        MissionStatus.RUNNING,
        MissionStatus.WAITING_FOR_APPROVAL,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.PLANNING,
    },
    MissionStatus.CANCELLING: {
        MissionStatus.CANCELLED,
        MissionStatus.FAILED,
    },
    MissionStatus.NEEDS_ATTENTION: {
        MissionStatus.READY,
        MissionStatus.PLANNING,
        MissionStatus.RUNNING,
        MissionStatus.CANCELLING,
        MissionStatus.CANCELLED,
        MissionStatus.FAILED,
    },
    # Terminal states
    MissionStatus.COMPLETED: set(),
    MissionStatus.PARTIALLY_COMPLETED: set(),
    MissionStatus.CANCELLED: set(),
    MissionStatus.FAILED: set(),
}


def validate_mission_transition(from_status: MissionStatus, to_status: MissionStatus) -> bool:
    """Validate whether a mission lifecycle transition is legally allowed.

    Raises:
        ValueError: If the transition violates the deterministic transition matrix.
    """
    if from_status == to_status:
        return True
    allowed = VALID_MISSION_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Illegal mission state transition: '{from_status.value}' -> '{to_status.value}'"
        )
    return True


@dataclass
class AcceptanceCriterion:
    """Explicit, verifiable success criterion for a mission outcome."""
    criterion_id: str
    description: str
    status: AcceptanceStatus = AcceptanceStatus.UNVERIFIED
    required: bool = True
    evidence: Dict[str, Any] = field(default_factory=dict)
    verified_at: Optional[float] = None
    external_dependency: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": sanitize_content(self.description),
            "status": self.status.value,
            "required": self.required,
            "evidence": self.evidence,
            "verified_at": self.verified_at,
            "external_dependency": self.external_dependency,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AcceptanceCriterion:
        return cls(
            criterion_id=data["criterion_id"],
            description=data["description"],
            status=AcceptanceStatus(data.get("status", AcceptanceStatus.UNVERIFIED.value)),
            required=bool(data.get("required", True)),
            evidence=dict(data.get("evidence", {})),
            verified_at=data.get("verified_at"),
            external_dependency=data.get("external_dependency"),
        )


@dataclass
class MissionBudget:
    """Bounded operational resource and time limits for a mission."""
    max_tasks: int = 20
    max_decomposition_depth: int = 3
    max_replan_attempts: int = 3
    max_retries_per_task: int = 2
    max_concurrent_tasks: int = 1
    deadline_s: Optional[float] = None
    current_replan_count: int = 0
    tasks_executed: int = 0

    def is_replan_exhausted(self) -> bool:
        return self.current_replan_count >= self.max_replan_attempts

    def is_task_budget_exhausted(self) -> bool:
        return self.tasks_executed >= self.max_tasks

    def is_deadline_exceeded(self, started_at: float) -> bool:
        if self.deadline_s is None or started_at <= 0:
            return False
        return (time.time() - started_at) > self.deadline_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_tasks": self.max_tasks,
            "max_decomposition_depth": self.max_decomposition_depth,
            "max_replan_attempts": self.max_replan_attempts,
            "max_retries_per_task": self.max_retries_per_task,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "deadline_s": self.deadline_s,
            "current_replan_count": self.current_replan_count,
            "tasks_executed": self.tasks_executed,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MissionBudget:
        return cls(
            max_tasks=int(data.get("max_tasks", 20)),
            max_decomposition_depth=int(data.get("max_decomposition_depth", 3)),
            max_replan_attempts=int(data.get("max_replan_attempts", 3)),
            max_retries_per_task=int(data.get("max_retries_per_task", 2)),
            max_concurrent_tasks=int(data.get("max_concurrent_tasks", 1)),
            deadline_s=data.get("deadline_s"),
            current_replan_count=int(data.get("current_replan_count", 0)),
            tasks_executed=int(data.get("tasks_executed", 0)),
        )


@dataclass
class MissionTask:
    """Executable task within a MissionPlan DAG."""
    task_id: str
    description: str
    intended_result: str
    dependencies: List[str] = field(default_factory=list)
    capability_id: Optional[str] = None
    inputs: Dict[str, Any] = field(default_factory=dict)
    preconditions: List[str] = field(default_factory=list)
    required_permissions: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    approval_checkpoint: bool = False
    approval_reason: str = ""
    status: MissionTaskStatus = MissionTaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    timeout_s: float = 30.0
    verification_method: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    is_idempotent: bool = True
    side_effects_occurred: bool = False
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "description": sanitize_content(self.description),
            "intended_result": sanitize_content(self.intended_result),
            "dependencies": list(self.dependencies),
            "capability_id": self.capability_id,
            "inputs": self.inputs,
            "preconditions": list(self.preconditions),
            "required_permissions": list(self.required_permissions),
            "risk_level": self.risk_level.value,
            "approval_checkpoint": self.approval_checkpoint,
            "approval_reason": sanitize_content(self.approval_reason),
            "status": self.status.value,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "timeout_s": self.timeout_s,
            "verification_method": self.verification_method,
            "evidence": self.evidence,
            "output": self.output,
            "error": sanitize_content(self.error) if self.error else None,
            "is_idempotent": self.is_idempotent,
            "side_effects_occurred": self.side_effects_occurred,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MissionTask:
        return cls(
            task_id=data["task_id"],
            description=data.get("description", ""),
            intended_result=data.get("intended_result", ""),
            dependencies=list(data.get("dependencies", [])),
            capability_id=data.get("capability_id"),
            inputs=dict(data.get("inputs", {})),
            preconditions=list(data.get("preconditions", [])),
            required_permissions=list(data.get("required_permissions", [])),
            risk_level=RiskLevel(data.get("risk_level", RiskLevel.LOW.value)),
            approval_checkpoint=bool(data.get("approval_checkpoint", False)),
            approval_reason=data.get("approval_reason", ""),
            status=MissionTaskStatus(data.get("status", MissionTaskStatus.PENDING.value)),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 2)),
            timeout_s=float(data.get("timeout_s", 30.0)),
            verification_method=data.get("verification_method", ""),
            evidence=dict(data.get("evidence", {})),
            output=data.get("output"),
            error=data.get("error"),
            is_idempotent=bool(data.get("is_idempotent", True)),
            side_effects_occurred=bool(data.get("side_effects_occurred", False)),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )


@dataclass
class MissionPlan:
    """A versioned dependency DAG of tasks to fulfill a mission objective."""
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    plan_version: int = 1
    tasks: List[MissionTask] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    replan_reason: Optional[str] = None
    materially_changed: bool = False

    def get_task(self, task_id: str) -> Optional[MissionTask]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "created_at": self.created_at,
            "replan_reason": sanitize_content(self.replan_reason) if self.replan_reason else None,
            "materially_changed": self.materially_changed,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MissionPlan:
        return cls(
            plan_id=data.get("plan_id", str(uuid.uuid4())),
            plan_version=int(data.get("plan_version", 1)),
            created_at=float(data.get("created_at", time.time())),
            replan_reason=data.get("replan_reason"),
            materially_changed=bool(data.get("materially_changed", False)),
            tasks=[MissionTask.from_dict(t) for t in data.get("tasks", [])],
        )


@dataclass
class Mission:
    """First-class durable mission object for goal-driven autonomous execution."""
    mission_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    objective: str = ""
    normalized_goal: str = ""
    explicit_constraints: List[str] = field(default_factory=list)
    inferred_constraints: List[str] = field(default_factory=list)
    acceptance_criteria: List[AcceptanceCriterion] = field(default_factory=list)
    plan: Optional[MissionPlan] = None
    mode: MissionMode = MissionMode.COPILOT
    status: MissionStatus = MissionStatus.CREATED
    status_reason: str = ""
    budget: MissionBudget = field(default_factory=MissionBudget)
    required_permissions: List[str] = field(default_factory=list)
    approved_plan_version: Optional[int] = None
    approved_tasks: Set[str] = field(default_factory=set)
    blockers: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    unresolved_questions: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    final_report: Optional[Dict[str, Any]] = None

    def transition_to(self, new_status: MissionStatus, reason: str = "") -> None:
        """Deterministically transition status, validating legal lifecycle progression."""
        validate_mission_transition(self.status, new_status)
        self.status = new_status
        self.status_reason = sanitize_content(reason)
        self.updated_at = time.time()

    def is_terminal(self) -> bool:
        return self.status in {
            MissionStatus.COMPLETED,
            MissionStatus.PARTIALLY_COMPLETED,
            MissionStatus.CANCELLED,
            MissionStatus.FAILED,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "objective": sanitize_content(self.objective),
            "normalized_goal": sanitize_content(self.normalized_goal),
            "explicit_constraints": [sanitize_content(c) for c in self.explicit_constraints],
            "inferred_constraints": [sanitize_content(c) for c in self.inferred_constraints],
            "acceptance_criteria": [ac.to_dict() for ac in self.acceptance_criteria],
            "plan": self.plan.to_dict() if self.plan else None,
            "mode": self.mode.value,
            "status": self.status.value,
            "status_reason": sanitize_content(self.status_reason),
            "budget": self.budget.to_dict(),
            "required_permissions": list(self.required_permissions),
            "approved_plan_version": self.approved_plan_version,
            "approved_tasks": list(self.approved_tasks),
            "blockers": [sanitize_content(b) for b in self.blockers],
            "assumptions": [sanitize_content(a) for a in self.assumptions],
            "unresolved_questions": [sanitize_content(q) for q in self.unresolved_questions],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "final_report": self.final_report,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Mission:
        plan_data = data.get("plan")
        return cls(
            mission_id=data.get("mission_id", str(uuid.uuid4())),
            objective=data.get("objective", ""),
            normalized_goal=data.get("normalized_goal", ""),
            explicit_constraints=list(data.get("explicit_constraints", [])),
            inferred_constraints=list(data.get("inferred_constraints", [])),
            acceptance_criteria=[
                AcceptanceCriterion.from_dict(ac)
                for ac in data.get("acceptance_criteria", [])
            ],
            plan=MissionPlan.from_dict(plan_data) if plan_data else None,
            mode=MissionMode(data.get("mode", MissionMode.COPILOT.value)),
            status=MissionStatus(data.get("status", MissionStatus.CREATED.value)),
            status_reason=data.get("status_reason", ""),
            budget=MissionBudget.from_dict(data.get("budget", {})),
            required_permissions=list(data.get("required_permissions", [])),
            approved_plan_version=data.get("approved_plan_version"),
            approved_tasks=set(data.get("approved_tasks", [])),
            blockers=list(data.get("blockers", [])),
            assumptions=list(data.get("assumptions", [])),
            unresolved_questions=list(data.get("unresolved_questions", [])),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            final_report=data.get("final_report"),
        )
