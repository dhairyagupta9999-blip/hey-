"""Jev Agent Orchestrator: Central Coordinator for General-Purpose Tasks.

Manages task creation, decomposition, deterministic routing, provider adapters,
agent-to-agent handoff, concurrency gating (2C/8GB RAM), independent verification,
cancellation, event dispatch, and SQLite-backed audit trails.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent_adapter import AgentAdapter
from agent_registry import AgentMetadata, AgentRegistry
from agent_result import AgentResult, AgentResultStatus
from agent_router import DeterministicAgentRouter, RoutingDecision, TaskRouter
from agent_task import (
    AgentTask,
    InvalidStateTransitionError,
    TaskPermission,
    TaskPriority,
    TaskStatus,
)
from event_bus import EventBus
from task_planner import TaskGraph, TaskPlanner
from task_storage import TaskStorage
from task_verifier import TaskVerifier, VerificationResult


@dataclass
class HandoffEnvelope:
    """Structured container for passing work and context between agents."""
    task_id: str
    source_agent: str
    target_agent: str
    result: AgentResult
    reason: str
    timestamp: float = field(default_factory=time.time)
    parent_task_id: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "result": self.result.to_dict(),
            "reason": self.reason,
            "timestamp": self.timestamp,
            "parent_task_id": self.parent_task_id,
            "context": self.context,
        }


class ConcurrencyController:
    """Resource governor tailored for 2 CPU cores / 8 GB RAM machines.
    
    Strictly bounds active local agent processes to prevent CPU contention.
    """

    def __init__(self, max_local_agents: int = 1, max_background_tasks: int = 2) -> None:
        self.max_local_agents = max_local_agents
        self.max_background_tasks = max_background_tasks
        self._lock = threading.Lock()
        self._active_local: int = 0
        self._active_background: int = 0

    @property
    def active_local_count(self) -> int:
        with self._lock:
            return self._active_local

    def acquire_local(self) -> bool:
        """Acquire execution slot for a local worker."""
        with self._lock:
            if self._active_local < self.max_local_agents:
                self._active_local += 1
                return True
            return False

    def release_local(self) -> None:
        """Release execution slot for a local worker."""
        with self._lock:
            if self._active_local > 0:
                self._active_local -= 1


class PermissionPolicy:
    """Enforces task permission boundaries before dispatching actions."""

    HIGH_RISK_PERMISSIONS = {
        TaskPermission.EXECUTE_COMMAND,
        TaskPermission.CHANGE_SETTINGS,
        TaskPermission.INSTALL_SOFTWARE,
    }

    @classmethod
    def evaluate(cls, task: AgentTask, agent: Optional[AgentMetadata]) -> Tuple[bool, List[str]]:
        """Determine whether the task is authorized to proceed with the selected agent."""
        violations: List[str] = []

        if agent is None:
            return False, ["No agent metadata provided."]

        # Check missing permissions
        for perm in task.permissions:
            if perm not in agent.permissions:
                violations.append(f"Agent '{agent.name}' lacks permission '{perm.value}'")

        # Check high-risk approval requirement
        has_high_risk = any(p in cls.HIGH_RISK_PERMISSIONS for p in task.permissions)
        if (task.requires_approval or has_high_risk) and task.approval_state != "APPROVED":
            violations.append("Task contains sensitive permissions and has not received human approval.")

        return len(violations) == 0, violations


class JevOrchestrator:
    """Master orchestrator for Hey Jev agent operations."""

    def __init__(
        self,
        storage: Optional[TaskStorage] = None,
        registry: Optional[AgentRegistry] = None,
        router: Optional[TaskRouter] = None,
        planner: Optional[TaskPlanner] = None,
        verifier: Optional[TaskVerifier] = None,
        event_bus: Optional[EventBus] = None,
        max_local_agents: int = 1,
    ) -> None:
        self.storage = storage or TaskStorage()
        self.registry = registry or AgentRegistry()
        self.router = router or DeterministicAgentRouter()
        self.planner = planner or TaskPlanner()
        self.verifier = verifier or TaskVerifier()
        self.event_bus = event_bus or EventBus()
        self.concurrency = ConcurrencyController(max_local_agents=max_local_agents)

    def create_task(
        self,
        goal: str,
        description: str = "",
        parent_task_id: Optional[str] = None,
        permissions: Optional[List[TaskPermission]] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        context: Optional[Dict[str, Any]] = None,
        requires_approval: bool = False,
    ) -> AgentTask:
        """Create and persist a new AgentTask from natural-language goal."""
        task = AgentTask(
            goal=goal,
            description=description or goal,
            parent_task_id=parent_task_id,
            permissions=permissions or [],
            priority=priority,
            context=dict(context or {}),
            requires_approval=requires_approval,
            status=TaskStatus.QUEUED,
        )

        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.created",
            new_status=task.status.value,
            details={"goal": goal, "permissions": [p.value for p in task.permissions]},
        )
        self.event_bus.emit("task.created", task.task_id, data={"goal": goal})
        return task

    def plan_and_decompose(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> TaskGraph:
        """Decompose a high-level goal into a persistent TaskGraph."""
        graph = self.planner.decompose_goal(goal, context=context)

        # Persist all tasks in graph
        for task in graph.tasks.values():
            self.storage.save_task(task)
            self.storage.record_audit(
                task_id=task.task_id,
                event_type="task.planned",
                new_status=task.status.value,
                details={"parent_task_id": task.parent_task_id, "goal": task.goal},
            )
            self.event_bus.emit("task.created", task.task_id, data={"goal": task.goal})

        return graph

    def route_task(self, task: AgentTask) -> RoutingDecision:
        """Select the best agent for the task and update task assignment."""
        decision = self.router.route_task(task, self.registry)
        task.current_agent = decision.selected_agent
        if decision.selected_agent not in task.assigned_agents:
            task.assigned_agents.append(decision.selected_agent)

        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.routed",
            agent_name=decision.selected_agent,
            details={"score": decision.score, "reason": decision.reason},
        )
        self.event_bus.emit(
            "task.progress",
            task.task_id,
            agent_name=decision.selected_agent,
            data={"action": "routed", "decision": decision.selected_agent},
        )
        return decision

    def execute_task(self, task: AgentTask) -> AgentResult:
        """Execute a task through its assigned provider adapter with safety gates and verification."""
        # 1. Routing if current_agent not set
        if not task.current_agent:
            self.route_task(task)

        agent_meta = self.registry.get_agent(task.current_agent)  # type: ignore
        adapter = self.registry.get_adapter(task.current_agent)  # type: ignore

        # 2. Permission and approval verification
        authorized, violations = PermissionPolicy.evaluate(task, agent_meta)
        if not authorized:
            task.transition_to(TaskStatus.BLOCKED, reason="Permission violation or approval required")
            self.storage.save_task(task)
            self.storage.record_audit(
                task_id=task.task_id,
                event_type="task.permission_denied",
                old_status="QUEUED",
                new_status="BLOCKED",
                agent_name=task.current_agent or "",
                details={"violations": violations},
            )
            self.event_bus.emit(
                "task.waiting",
                task.task_id,
                agent_name=task.current_agent or "",
                data={"reason": "approval_required", "violations": violations},
            )
            return AgentResult(
                status=AgentResultStatus.BLOCKED,
                error=f"Task blocked: {'; '.join(violations)}",
                summary="Blocked by permissions policy",
                confidence=0.0,
            )

        # 3. Concurrency acquisition (protect 2C/8GB RAM)
        if not self.concurrency.acquire_local():
            task.transition_to(TaskStatus.WAITING, reason="Waiting for available local CPU capacity")
            self.storage.save_task(task)
            self.event_bus.emit(
                "task.waiting",
                task.task_id,
                agent_name=task.current_agent or "",
                data={"reason": "concurrency_limit_reached"},
            )
            return AgentResult(
                status=AgentResultStatus.BLOCKED,
                error="Concurrency limit reached (max_local_agents = 1)",
                summary="Queued awaiting free worker slot",
                confidence=0.0,
            )

        try:
            # 4. Transition to RUNNING
            old_status = task.status.value
            task.transition_to(TaskStatus.RUNNING, reason=f"Started execution with {task.current_agent}")
            self.storage.save_task(task)
            self.storage.record_audit(
                task_id=task.task_id,
                event_type="task.started",
                old_status=old_status,
                new_status=TaskStatus.RUNNING.value,
                agent_name=task.current_agent or "",
            )
            self.event_bus.emit("task.started", task.task_id, agent_name=task.current_agent or "")

            # 5. Dispatch to adapter if bound, or synthesize mock result
            if adapter:
                adapter.submit_task(task)
                # If adapter already produced result or mock:
                res = adapter.get_task_result(task.task_id)
                if not res:
                    res = AgentResult(
                        result="Adapter accepted task",
                        summary=f"Task dispatched to {task.current_agent}",
                        status=AgentResultStatus.SUCCESS,
                        confidence=0.95,
                    )
            else:
                res = AgentResult(
                    result=f"Simulated execution for {task.goal}",
                    summary=f"Executed by {task.current_agent}",
                    status=AgentResultStatus.SUCCESS,
                    confidence=0.90,
                )

            # 6. Verification layer (independent deterministic verification)
            task.transition_to(TaskStatus.VERIFYING, reason="Validating execution results")
            self.storage.save_task(task)
            self.storage.record_audit(
                task_id=task.task_id,
                event_type="task.verifying",
                old_status=TaskStatus.RUNNING.value,
                new_status=TaskStatus.VERIFYING.value,
                agent_name=task.current_agent or "",
            )

            v_result = self.verifier.verify(task, res)

            if v_result.passed:
                task.transition_to(TaskStatus.COMPLETED, reason="Verification checks passed")
                task.outputs = res.to_dict()
                task.confidence = res.confidence
                task.next_action = res.next_action
                self.storage.save_task(task)
                self.storage.save_result(task.task_id, task.current_agent or "", res)
                self.storage.record_audit(
                    task_id=task.task_id,
                    event_type="task.completed",
                    old_status=TaskStatus.VERIFYING.value,
                    new_status=TaskStatus.COMPLETED.value,
                    agent_name=task.current_agent or "",
                    details={"verification": v_result.summary},
                )
                self.event_bus.emit("task.completed", task.task_id, agent_name=task.current_agent or "")
            else:
                task.transition_to(TaskStatus.FAILED, reason=f"Verification failed: {v_result.summary}")
                task.add_error(f"Verification failed: {'; '.join(v_result.failures)}")
                self.storage.save_task(task)
                self.storage.record_audit(
                    task_id=task.task_id,
                    event_type="task.verification_failed",
                    old_status=TaskStatus.VERIFYING.value,
                    new_status=TaskStatus.FAILED.value,
                    agent_name=task.current_agent or "",
                    details={"failures": v_result.failures},
                )
                self.event_bus.emit(
                    "task.verification_failed",
                    task.task_id,
                    agent_name=task.current_agent or "",
                    data={"failures": v_result.failures},
                )
                self.event_bus.emit("task.failed", task.task_id, agent_name=task.current_agent or "")

            return res

        finally:
            self.concurrency.release_local()

    def handoff(
        self,
        task_id: str,
        source_agent: str,
        target_agent: str,
        result: AgentResult,
        reason: str = "",
    ) -> HandoffEnvelope:
        """Execute a structured agent-to-agent handoff."""
        task = self.storage.get_task(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found in storage.")

        envelope = HandoffEnvelope(
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            result=result,
            reason=reason or f"Handoff from {source_agent} to {target_agent}",
            parent_task_id=task.parent_task_id,
            context=dict(task.context),
        )

        # Update task state
        if source_agent not in task.assigned_agents:
            task.assigned_agents.append(source_agent)
        task.current_agent = target_agent
        if target_agent not in task.assigned_agents:
            task.assigned_agents.append(target_agent)
        task.context.setdefault("handoff_history", []).append(envelope.to_dict())

        # If previous result had recommendations or next_action, propagate them
        if result.recommendations:
            task.recommendations.extend(result.recommendations)
        if result.next_action:
            task.next_action = result.next_action
        if result.error:
            task.add_error(result.error)

        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.agent_handoff",
            agent_name=source_agent,
            details={
                "from": source_agent,
                "to": target_agent,
                "reason": reason,
                "confidence": result.confidence,
            },
        )
        self.event_bus.emit(
            "task.agent_handoff",
            task.task_id,
            agent_name=source_agent,
            data={"from": source_agent, "to": target_agent, "reason": reason},
        )
        return envelope

    def cancel_task(self, task_id: str, reason: str = "User requested cancellation") -> bool:
        """Cancel a running or queued task."""
        task = self.storage.get_task(task_id)
        if not task:
            return False

        old_status = task.status.value

        # Notify adapter if bound
        if task.current_agent:
            adapter = self.registry.get_adapter(task.current_agent)
            if adapter:
                try:
                    adapter.cancel_task(task_id, reason=reason)
                except Exception:
                    pass

        task.request_cancellation(reason=reason)
        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.cancelled",
            old_status=old_status,
            new_status=TaskStatus.CANCELLED.value,
            agent_name=task.current_agent or "",
            details={"reason": reason},
        )
        self.event_bus.emit(
            "task.cancelled",
            task.task_id,
            agent_name=task.current_agent or "",
            data={"reason": reason},
        )
        return True

    def recover_unfinished_tasks(self) -> List[AgentTask]:
        """Find and reconcile tasks that were running when the process terminated."""
        unfinished = self.storage.get_unfinished_tasks()
        recovered: List[AgentTask] = []

        for task in unfinished:
            if task.status in (TaskStatus.RUNNING, TaskStatus.PLANNING, TaskStatus.VERIFYING):
                old_status = task.status.value
                # Mark as WAITING or FAILED for clean resume
                task.status = TaskStatus.WAITING
                task.context["interrupted_by_restart"] = True
                self.storage.save_task(task)
                self.storage.record_audit(
                    task_id=task.task_id,
                    event_type="task.recovered",
                    old_status=old_status,
                    new_status=TaskStatus.WAITING.value,
                    details={"notice": "Task recovered across application restart"},
                )
                recovered.append(task)
            elif task.status in (TaskStatus.QUEUED, TaskStatus.BLOCKED, TaskStatus.WAITING):
                recovered.append(task)

        return recovered
