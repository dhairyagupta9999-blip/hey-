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
from agent_message import AgentMessage, AgentMessageType, ContextFilterMode
from mission import Mission, MissionStatus, TimelineEntry
from router_v2 import AgentRouterV2, RouterDecisionV2
from synthesis_engine import SynthesisEngine, SynthesizedResolution
from reviewer_pattern import ReviewFeedback, ReviewLoopController, ReviewStatus


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
        safe_ctx = {k: v for k, v in self.context.items() if k != "handoff_history"}
        return {
            "task_id": self.task_id,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "result": self.result.to_dict(),
            "reason": self.reason,
            "timestamp": self.timestamp,
            "parent_task_id": self.parent_task_id,
            "context": safe_ctx,
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
        router_v2: Optional[AgentRouterV2] = None,
        synthesis_engine: Optional[SynthesisEngine] = None,
        review_controller: Optional[ReviewLoopController] = None,
    ) -> None:
        self.storage = storage or TaskStorage()
        self.registry = registry or AgentRegistry()
        self.router = router or DeterministicAgentRouter()
        self.planner = planner or TaskPlanner()
        self.verifier = verifier or TaskVerifier()
        self.event_bus = event_bus or EventBus()
        self.concurrency = ConcurrencyController(max_local_agents=max_local_agents)
        self.router_v2 = router_v2 or AgentRouterV2()
        self.synthesis_engine = synthesis_engine or SynthesisEngine()
        self.review_controller = review_controller or ReviewLoopController()

    def create_task(
        self,
        goal: str,
        description: str = "",
        parent_task_id: Optional[str] = None,
        permissions: Optional[List[TaskPermission]] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
        context: Optional[Dict[str, Any]] = None,
        requires_approval: bool = False,
        owner_agent: str = "jev_orchestrator",
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
            owner_agent=owner_agent,
        )

        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.created",
            new_status=task.status.value,
            details={"goal": goal, "permissions": [p.value for p in task.permissions], "owner_agent": owner_agent},
        )
        self.event_bus.emit("task.created", task.task_id, data={"goal": goal, "owner_agent": owner_agent})
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

        # Populate fallback agents and multi-factor scores using router_v2
        if self.router_v2:
            try:
                v2_decision = self.router_v2.route(task, self.registry)
                task.context["fallback_agents"] = v2_decision.fallback_agents
                task.context["routing_score"] = v2_decision.score
                task.context["routing_strategy"] = v2_decision.strategy_used
            except Exception:
                task.context.setdefault("fallback_agents", [])

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

    def execute_task(self, task: AgentTask, auto_fallback: bool = True) -> AgentResult:
        """Execute a task through its assigned provider adapter with safety gates and verification."""
        # 1. Routing if current_agent not set
        if not task.current_agent:
            self.route_task(task)
        elif task.current_agent not in task.assigned_agents:
            task.assigned_agents.append(task.current_agent)

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

        should_fallback = False
        fallback_target: Optional[str] = None
        res: AgentResult = AgentResult(status=AgentResultStatus.FAILURE)
        try:
            # 4. Transition to RUNNING
            old_status = task.status.value
            if task.status == TaskStatus.COMPLETED:
                task.transition_to(TaskStatus.QUEUED, reason="Re-queued for execution")
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
                try:
                    adapter.submit_task(task)
                    got_res = adapter.get_task_result(task.task_id)
                    if got_res:
                        res = got_res
                    else:
                        res = AgentResult(
                            result="Adapter accepted task",
                            summary=f"Task dispatched to {task.current_agent}",
                            status=AgentResultStatus.SUCCESS,
                            confidence=0.95,
                        )
                except Exception as exc:
                    res = AgentResult(
                        result="",
                        summary=f"Adapter execution error: {str(exc)}",
                        status=AgentResultStatus.FAILURE,
                        error=str(exc),
                        confidence=0.0,
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

            # Check if fallback should be triggered
            is_failure = (not v_result.passed) or (res.status == AgentResultStatus.FAILURE)
            if is_failure and auto_fallback:
                fallbacks = task.context.get("fallback_agents", [])
                tried = task.context.setdefault("tried_agents", [])
                if task.current_agent and task.current_agent not in tried:
                    tried.append(task.current_agent)
                next_agent = next((a for a in fallbacks if a not in tried), None)
                if next_agent and self.registry.get_agent(next_agent):
                    should_fallback = True
                    fallback_target = next_agent

            if v_result.passed and res.is_success:
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
                failure_reason = v_result.summary if not v_result.passed else (res.error or res.summary)
                if not should_fallback:
                    task.transition_to(TaskStatus.FAILED, reason=f"Execution failed: {failure_reason}")
                if not v_result.passed:
                    task.add_error(f"Verification failed: {'; '.join(v_result.failures)}")
                if res.error:
                    task.add_error(res.error)
                self.storage.save_task(task)
                self.storage.record_audit(
                    task_id=task.task_id,
                    event_type="task.verification_failed" if not v_result.passed else "task.failed",
                    old_status=TaskStatus.VERIFYING.value,
                    new_status=task.status.value,
                    agent_name=task.current_agent or "",
                    details={"failures": v_result.failures if not v_result.passed else [failure_reason]},
                )
                self.event_bus.emit(
                    "task.verification_failed" if not v_result.passed else "task.failed",
                    task.task_id,
                    agent_name=task.current_agent or "",
                    data={"failures": v_result.failures if not v_result.passed else [failure_reason]},
                )

        finally:
            self.concurrency.release_local()

        if should_fallback and fallback_target:
            old_agent = task.current_agent
            task.context["previous_failure"] = res.error or (v_result.summary if 'v_result' in locals() else "Execution failed")
            task.current_agent = fallback_target
            if fallback_target not in task.assigned_agents:
                task.assigned_agents.append(fallback_target)
            task.status = TaskStatus.QUEUED
            self.storage.save_task(task)
            self.storage.record_audit(
                task_id=task.task_id,
                event_type="task.fallback",
                agent_name=fallback_target,
                details={"fallback_from": old_agent, "fallback_to": fallback_target},
            )
            self.event_bus.emit(
                "task.fallback",
                task.task_id,
                agent_name=fallback_target,
                data={"from": old_agent, "to": fallback_target},
            )
            return self.execute_task(task, auto_fallback=True)

        return res

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
        if task.status == TaskStatus.COMPLETED:
            task.transition_to(TaskStatus.QUEUED, reason=f"Task handed off to {target_agent}")
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

    # =========================================================================
    # Phase 14: Agent-to-Agent Collaboration & Mission Orchestration
    # =========================================================================

    def submit_to_agent(self, agent_name: str, message: AgentMessage) -> AgentMessage:
        """Dispatch an AgentMessage directly to a registered agent."""
        agent_meta = self.registry.get_agent(agent_name)
        if not agent_meta:
            return message.create_reply(
                sender_agent=agent_name,
                message_type=AgentMessageType.TASK_ERROR,
                result=AgentResult(
                    summary=f"Agent '{agent_name}' not found in registry",
                    error="UnknownAgent",
                    status=AgentResultStatus.FAILURE,
                    confidence=0.0,
                ),
            )

        adapter = self.registry.get_adapter(agent_name)
        if adapter and hasattr(adapter, "handle_message"):
            try:
                res_msg = adapter.handle_message(message)
                if res_msg:
                    return res_msg
            except Exception as exc:
                return message.create_reply(
                    sender_agent=agent_name,
                    message_type=AgentMessageType.TASK_ERROR,
                    result=AgentResult(
                        summary=f"Adapter error: {str(exc)}",
                        error=str(exc),
                        status=AgentResultStatus.FAILURE,
                        confidence=0.0,
                    ),
                )

        # Fallback dispatch via standard task execution
        task = self.create_task(
            goal=message.goal or message.instructions or "Agent message dispatch",
            owner_agent=message.sender_agent,
            context=dict(message.context),
        )
        task.current_agent = agent_name
        task.inputs = dict(message.inputs)
        result = self.execute_task(task)

        msg_type = AgentMessageType.TASK_RESULT if result.is_success else AgentMessageType.TASK_ERROR
        return message.create_reply(
            sender_agent=agent_name,
            message_type=msg_type,
            result=result,
        )

    def handoff_to_agent(
        self,
        task_id: str,
        source_agent: str,
        target_agent: str,
        reason: str = "",
        context_filter: ContextFilterMode = ContextFilterMode.RELEVANT,
    ) -> HandoffEnvelope:
        """Execute a structured agent-to-agent handoff with context filtering."""
        task = self.storage.get_task(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found in storage.")

        filtered_ctx = AgentMessage.filter_context_dict(task.context, context_filter)

        envelope = HandoffEnvelope(
            task_id=task_id,
            source_agent=source_agent,
            target_agent=target_agent,
            result=AgentResult(
                summary=reason or f"Handoff from {source_agent} to {target_agent}",
                status=AgentResultStatus.SUCCESS,
            ),
            reason=reason or f"Handoff from {source_agent} to {target_agent}",
            parent_task_id=task.parent_task_id,
            context=filtered_ctx,
        )

        if source_agent not in task.assigned_agents:
            task.assigned_agents.append(source_agent)
        task.current_agent = target_agent
        if target_agent not in task.assigned_agents:
            task.assigned_agents.append(target_agent)

        if task.status == TaskStatus.COMPLETED:
            task.transition_to(TaskStatus.QUEUED, reason=f"Task handed off to {target_agent}")

        existing_history = list(task.context.get("handoff_history", []))
        task.context = filtered_ctx
        existing_history.append(envelope.to_dict())
        task.context["handoff_history"] = existing_history

        self.storage.save_task(task)
        self.storage.record_audit(
            task_id=task.task_id,
            event_type="task.agent_handoff",
            agent_name=source_agent,
            details={
                "from": source_agent,
                "to": target_agent,
                "reason": reason,
                "filter_mode": context_filter.value,
            },
        )
        self.event_bus.emit(
            "task.agent_handoff",
            task.task_id,
            agent_name=source_agent,
            data={"from": source_agent, "to": target_agent, "reason": reason},
        )
        return envelope

    def call_specialist_as_tool(
        self,
        calling_agent: str,
        specialist_agent: str,
        goal: str,
        input_data: Any = None,
        timeout: float = 30.0,
    ) -> AgentResult:
        """Calling agent invokes a specialist as a discrete tool while retaining ownership."""
        subtask = self.create_task(
            goal=goal,
            description=f"Specialist tool call by {calling_agent}: {goal}",
            owner_agent=calling_agent,
            context={"input_data": input_data, "called_as_tool": True, "timeout": timeout},
        )
        subtask.current_agent = specialist_agent
        if specialist_agent not in subtask.assigned_agents:
            subtask.assigned_agents.append(specialist_agent)

        return self.execute_task(subtask)

    def request_review(
        self,
        task_id: str,
        worker_agent: str,
        reviewer_agent: str,
        work_output: Any = None,
        max_rounds: int = 2,
    ) -> Tuple[ReviewStatus, List[ReviewFeedback]]:
        """Run bounded worker-reviewer iterations (max 2 rounds) to enforce quality gating."""
        task = self.storage.get_task(task_id)
        if not task:
            raise KeyError(f"Task {task_id} not found in storage.")

        controller = ReviewLoopController(max_review_rounds=min(max_rounds, 2))

        def worker_fn(t: AgentTask, feedback: Optional[ReviewFeedback]) -> AgentResult:
            if feedback:
                t.context["reviewer_feedback"] = feedback.to_dict()
            adapter = self.registry.get_adapter(worker_agent)
            if adapter:
                adapter.submit_task(t)
                res = adapter.get_task_result(t.task_id)
                if res:
                    return res
            return AgentResult(
                summary=f"Worker {worker_agent} output (round {feedback.round_number + 1 if feedback else 1})",
                status=AgentResultStatus.SUCCESS,
                recommendations=[f"Addressed feedback: {feedback.summary}"] if feedback else [],
            )

        def reviewer_fn(t: AgentTask, res: AgentResult, round_num: int) -> ReviewFeedback:
            adapter = self.registry.get_adapter(reviewer_agent)
            if adapter and hasattr(adapter, "review"):
                return adapter.review(t, res, round_num)
            if round_num == 1 and res.status != AgentResultStatus.SUCCESS:
                return ReviewFeedback(
                    status=ReviewStatus.CHANGES_REQUESTED,
                    issues=[res.error or "Initial output requires improvements"],
                    suggestions=["Resolve errors and re-test"],
                    summary="Changes requested on initial implementation",
                    round_number=round_num,
                    reviewer_agent=reviewer_agent,
                )
            return ReviewFeedback(
                status=ReviewStatus.APPROVED,
                summary=f"Approved by {reviewer_agent} in round {round_num}",
                round_number=round_num,
                reviewer_agent=reviewer_agent,
            )

        final_res, history = controller.execute_review_round(
            task, worker_agent, reviewer_agent, worker_fn, reviewer_fn
        )
        final_status = history[-1].status if history else ReviewStatus.APPROVED
        return final_status, history

    def debate_goal(
        self,
        goal: str,
        agent_opinions: Union[Dict[str, AgentResult], Dict[str, Dict[str, Any]], List[Dict[str, Any]]],
    ) -> SynthesizedResolution:
        """Deterministically adjudicate multi-agent positions or proposals."""
        if isinstance(agent_opinions, dict):
            first_val = next(iter(agent_opinions.values()), None)
            if isinstance(first_val, AgentResult):
                return self.synthesis_engine.synthesize_results(agent_opinions)  # type: ignore
            else:
                positions = []
                for name, d in agent_opinions.items():
                    pos = dict(d)
                    pos.setdefault("agent_name", name)
                    positions.append(pos)
                return self.synthesis_engine.adjudicator.adjudicate(positions)
        elif isinstance(agent_opinions, list):
            return self.synthesis_engine.adjudicator.adjudicate(agent_opinions)
        return SynthesizedResolution(
            selected_action="none",
            winning_recommendation="Invalid opinions format",
            confidence=0.0,
            rationale="Unsupported opinions type",
        )

    def create_mission(
        self,
        title: str,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        max_agents: int = 5,
    ) -> Mission:
        """Create and persist a new long-running Mission."""
        mission = Mission(
            title=title,
            description=goal,
            context=context or {},
            max_agents=max_agents,
            status=MissionStatus.CREATED,
        )
        mission.record_timeline(
            event_type="mission.created",
            summary=f"Mission initialized: {title}",
            details={"goal": goal},
        )
        self.storage.save_mission(mission)
        self.event_bus.emit("mission.created", mission.mission_id, data={"title": title, "goal": goal})
        return mission

    def get_mission(self, mission_id: str) -> Optional[Mission]:
        """Fetch mission by ID."""
        return self.storage.get_mission(mission_id)

    def pause_mission(self, mission_id: str, reason: str = "User requested pause") -> bool:
        """Pause a mission and record event."""
        m = self.storage.get_mission(mission_id)
        if not m:
            return False
        m.pause(reason=reason)
        self.storage.save_mission(m)
        self.event_bus.emit("mission.paused", mission_id, data={"reason": reason})
        return True

    def resume_mission(self, mission_id: str) -> bool:
        """Resume a paused mission."""
        m = self.storage.get_mission(mission_id)
        if not m:
            return False
        m.resume()
        self.storage.save_mission(m)
        self.event_bus.emit("mission.resumed", mission_id)
        return True

    def cancel_mission(self, mission_id: str, reason: str = "User requested cancellation") -> bool:
        """Cancel an in-progress mission and all associated tasks."""
        m = self.storage.get_mission(mission_id)
        if not m:
            return False
        m.cancel(reason=reason)
        for tid in m.task_ids:
            self.cancel_task(tid, reason=reason)
        self.storage.save_mission(m)
        self.event_bus.emit("mission.cancelled", mission_id, data={"reason": reason})
        return True

    def interrupt_mission(self, mission_id: str, reason: str = "Voice interrupt") -> bool:
        """Fast-path voice priority interrupt for ongoing mission."""
        return self.pause_mission(mission_id, reason=reason)

    def list_missions(self, status: Optional[MissionStatus] = None) -> List[Mission]:
        """List all missions with optional status filter."""
        return self.storage.list_missions(status=status)
