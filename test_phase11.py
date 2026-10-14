"""Comprehensive Test Suite for Phase 11: Jev Agent Harness Foundation.

Verifies all 14 Acceptance Test Areas:
1. Task creation & defaults
2. Explicit state machine valid transitions
3. Invalid state transitions raise error
4. Task persistence with SQLite
5. Restart recovery of unfinished tasks
6. TaskGraph DAG, dependencies & cycle prevention
7. Task decomposition via TaskPlanner
8. Task cancellation & adapter propagation
9. Agent registry, discovery & health updates
10. Deterministic agent routing
11. Structured agent-to-agent handoff
12. AgentResult normalization & serialization
13. EventBus emission & wildcard subscriptions
14. Permissions foundation & gating
15. Concurrency control (max_local_agents = 1)
16. Deterministic TaskVerifier separate from self-reporting
17. AntigravityAdapter local environment integration
"""
import os
import tempfile
import pytest

from agent_task import (
    AgentTask,
    InvalidStateTransitionError,
    TaskPermission,
    TaskPriority,
    TaskStatus,
)
from agent_result import AgentResult, AgentResultStatus
from agent_adapter import AgentAdapter
from antigravity_adapter import AntigravityAdapter
from agent_registry import AgentMetadata, AgentRegistry
from agent_router import DeterministicAgentRouter, RoutingDecision
from task_planner import CyclicDependencyError, TaskGraph, TaskPlanner
from task_storage import TaskStorage
from event_bus import EventBus, TaskEvent
from task_verifier import TaskVerifier, VerificationResult
from orchestrator import ConcurrencyController, JevOrchestrator, PermissionPolicy


class MockTestAdapter(AgentAdapter):
    """Test adapter recording lifecycle calls."""

    def __init__(self, name: str = "mock_adapter") -> None:
        self.name = name
        self.submitted: list[str] = []
        self.cancelled: list[str] = []
        self.result_to_return: AgentResult | None = None

    def submit_task(self, task: AgentTask) -> bool:
        self.submitted.append(task.task_id)
        return True

    def get_task_status(self, task_id: str) -> TaskStatus:
        return TaskStatus.RUNNING

    def stream_agent_output(self, task_id: str):
        yield f"output from {self.name}"

    def cancel_task(self, task_id: str, reason: str = "") -> bool:
        self.cancelled.append(task_id)
        return True

    def get_task_result(self, task_id: str) -> AgentResult | None:
        return self.result_to_return

    def approve_action(self, task_id: str, action_id: str, approved: bool) -> bool:
        return True


class TestPhase11AgentHarness:

    def test_01_task_creation_and_defaults(self):
        """Verify universal AgentTask creation, IDs, timestamps, and default attributes."""
        task = AgentTask(goal="Organize Downloads folder")
        assert task.task_id.startswith("task_")
        assert task.goal == "Organize Downloads folder"
        assert task.status == TaskStatus.QUEUED
        assert task.priority == TaskPriority.NORMAL
        assert "created" in task.timestamps
        assert "QUEUED" in task.timestamps
        assert task.retry_count == 0
        assert task.requires_approval is False
        assert task.is_cancelled() is False

    def test_02_explicit_state_machine_valid_transitions(self):
        """Verify strict progression through valid lifecycle states."""
        task = AgentTask(goal="Diagnose app issue")
        assert task.status == TaskStatus.QUEUED

        task.transition_to(TaskStatus.PLANNING, reason="Decomposing goal")
        assert task.status == TaskStatus.PLANNING
        assert "PLANNING" in task.timestamps

        task.transition_to(TaskStatus.RUNNING, reason="Executing plan")
        assert task.status == TaskStatus.RUNNING
        assert "RUNNING" in task.timestamps

        task.transition_to(TaskStatus.VERIFYING, reason="Validating outcome")
        assert task.status == TaskStatus.VERIFYING

        task.transition_to(TaskStatus.COMPLETED, reason="Verification passed")
        assert task.status == TaskStatus.COMPLETED

    def test_03_invalid_state_transitions_raise_error(self):
        """Verify invalid transitions raise InvalidStateTransitionError."""
        task = AgentTask(goal="Test invalid transition")
        # Direct QUEUED -> COMPLETED is illegal
        with pytest.raises(InvalidStateTransitionError):
            task.transition_to(TaskStatus.COMPLETED)

        # Transition to CANCELLED
        task.transition_to(TaskStatus.CANCELLED)
        assert task.status == TaskStatus.CANCELLED

        # CANCELLED is terminal: transition to RUNNING must fail
        with pytest.raises(InvalidStateTransitionError):
            task.transition_to(TaskStatus.RUNNING)

    def test_04_task_persistence_sqlite(self):
        """Verify SQLite persistence: save, retrieve, and verify fields survive."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            task = AgentTask(
                goal="Persist this task",
                description="Testing SQLite storage",
                priority=TaskPriority.HIGH,
                permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            )
            task.add_artifact("file", "C:/test.txt", "sample artifact")
            storage.save_task(task)

            loaded = storage.get_task(task.task_id)
            assert loaded is not None
            assert loaded.task_id == task.task_id
            assert loaded.goal == "Persist this task"
            assert loaded.priority == TaskPriority.HIGH
            assert TaskPermission.READ_FILES in loaded.permissions
            assert len(loaded.artifacts) == 1
            assert loaded.artifacts[0]["uri"] == "C:/test.txt"

            # Check audit logging
            storage.record_audit(task.task_id, "test.event", details={"foo": "bar"})
            audit = storage.get_audit_history(task.task_id)
            assert len(audit) == 1
            assert audit[0]["event_type"] == "test.event"
            assert audit[0]["details"]["foo"] == "bar"
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_05_restart_recovery_unfinished_tasks(self):
        """Verify recovering interrupted in-flight tasks upon restart."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            # Create tasks in different states
            t_running = AgentTask(task_id="t_running", goal="Running before crash", status=TaskStatus.RUNNING)
            t_queued = AgentTask(task_id="t_queued", goal="Queued before crash", status=TaskStatus.QUEUED)
            t_done = AgentTask(task_id="t_done", goal="Completed before crash", status=TaskStatus.COMPLETED)

            storage.save_task(t_running)
            storage.save_task(t_queued)
            storage.save_task(t_done)

            # Simulate restart with new Orchestrator instance pointing to same DB
            orchestrator = JevOrchestrator(storage=storage)
            recovered = orchestrator.recover_unfinished_tasks()

            # The running task was safely moved to WAITING with restart flag
            assert any(t.task_id == "t_running" for t in recovered)
            assert any(t.task_id == "t_queued" for t in recovered)
            assert not any(t.task_id == "t_done" for t in recovered)

            t_recovered = storage.get_task("t_running")
            assert t_recovered is not None
            assert t_recovered.status == TaskStatus.WAITING
            assert t_recovered.context.get("interrupted_by_restart") is True
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_06_task_graph_dag_and_cycle_prevention(self):
        """Verify DAG dependency management, topological sort, and cycle detection."""
        graph = TaskGraph()
        t1 = AgentTask(task_id="task_1", goal="Step 1")
        t2 = AgentTask(task_id="task_2", goal="Step 2")
        t3 = AgentTask(task_id="task_3", goal="Step 3")

        graph.add_task(t1)
        graph.add_task(t2)
        graph.add_task(t3)

        # t2 depends on t1; t3 depends on t2
        graph.add_dependency("task_2", "task_1")
        graph.add_dependency("task_3", "task_2")

        assert graph.is_acyclic() is True
        sort_order = graph.topological_sort()
        assert sort_order == ["task_1", "task_2", "task_3"]

        # Only task_1 is ready initially
        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].task_id == "task_1"

        # Complete task_1 -> task_2 becomes ready
        t1.transition_to(TaskStatus.RUNNING)
        t1.transition_to(TaskStatus.COMPLETED)
        ready_after_1 = graph.get_ready_tasks()
        assert len(ready_after_1) == 1
        assert ready_after_1[0].task_id == "task_2"

        # Attempting to add a cycle (task_1 depends on task_3) must raise CyclicDependencyError
        with pytest.raises(CyclicDependencyError):
            graph.add_dependency("task_1", "task_3")

    def test_07_task_decomposition_planner(self):
        """Verify TaskPlanner decomposes high-level goals into dependency-aware TaskGraphs."""
        planner = TaskPlanner()

        # Goal with error/debugging trigger
        graph = planner.decompose_goal("Fix my application login problem")
        assert len(graph.tasks) >= 7
        assert graph.is_acyclic() is True

        # Check expected child tasks exist
        child_goals = [t.goal.lower() for t in graph.tasks.values()]
        assert any("inspect" in g for g in child_goals)
        assert any("identify" in g for g in child_goals)
        assert any("implement" in g for g in child_goals)
        assert any("verify" in g for g in child_goals)

        # Research goal decomposes into parallel fan-out / fan-in graph
        res_graph = planner.decompose_goal("Find the best beachfront hotels in Australia")
        assert len(res_graph.tasks) >= 4
        assert res_graph.is_acyclic() is True

    def test_08_task_cancellation_and_adapter_propagation(self):
        """Verify task cancellation sets state, notifies adapter, and emits event."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            event_bus = EventBus()
            mock_adapter = MockTestAdapter("test_cancel")
            registry = AgentRegistry(seed_defaults=False)
            registry.register_agent(
                AgentMetadata(name="test_cancel", provider="test"),
                adapter=mock_adapter,
            )

            orchestrator = JevOrchestrator(storage=storage, registry=registry, event_bus=event_bus)

            # Record events
            cancelled_events = []
            event_bus.subscribe("task.cancelled", lambda e: cancelled_events.append(e))

            task = orchestrator.create_task(goal="Long search task")
            task.current_agent = "test_cancel"
            task.transition_to(TaskStatus.RUNNING)
            storage.save_task(task)

            # Cancel task
            success = orchestrator.cancel_task(task.task_id, reason="User said stop")
            assert success is True
            assert task.task_id in mock_adapter.cancelled

            updated = storage.get_task(task.task_id)
            assert updated is not None
            assert updated.status == TaskStatus.CANCELLED
            assert updated.cancellation_state.get("requested") is True
            assert updated.cancellation_state.get("reason") == "User said stop"
            assert len(cancelled_events) == 1
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_09_agent_registry_and_discovery(self):
        """Verify agent registry capabilities lookup, health updates, and adapter binding."""
        registry = AgentRegistry(seed_defaults=True)

        # Verify default agents
        assert registry.get_agent("antigravity") is not None
        assert registry.get_agent("tier3_pc") is not None
        assert registry.get_agent("claude") is not None

        # Find by capability
        coding_agents = registry.find_by_capability("coding")
        assert any(a.name == "antigravity" for a in coding_agents)

        review_agents = registry.find_by_capability("review")
        assert any(a.name == "claude" for a in review_agents)

        # Health status updates
        registry.update_health("antigravity", health_status="degraded", availability="degraded")
        agent = registry.get_agent("antigravity")
        assert agent is not None
        assert agent.health_status == "degraded"
        assert agent.availability == "degraded"

    def test_10_deterministic_agent_router(self):
        """Verify deterministic routing across coding, review, research, PC control, and file tasks."""
        router = DeterministicAgentRouter()
        registry = AgentRegistry(seed_defaults=True)

        # 1. Coding
        d1 = router.route_task(AgentTask(goal="Fix this Python error in auth.py"), registry)
        assert d1.selected_agent == "antigravity"

        # 2. Review
        d2 = router.route_task(AgentTask(goal="Review this implementation and architecture"), registry)
        assert d2.selected_agent == "claude"

        # 3. Research
        d3 = router.route_task(AgentTask(goal="Find the best beachfront hotels in Australia"), registry)
        assert d3.selected_agent == "research_agent"

        # 4. PC control
        d4 = router.route_task(AgentTask(goal="Open the fourth picture in viewer"), registry)
        assert d4.selected_agent == "tier3_pc"

        # 5. File organization
        d5 = router.route_task(AgentTask(goal="Organize my Downloads folder"), registry)
        assert d5.selected_agent == "windows_agent"

    def test_11_structured_agent_to_agent_handoff(self):
        """Verify structured handoff between agents preserves context and recommendations."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            event_bus = EventBus()
            orchestrator = JevOrchestrator(storage=storage, event_bus=event_bus)

            handoff_events = []
            event_bus.subscribe("task.agent_handoff", lambda e: handoff_events.append(e))

            task = orchestrator.create_task(goal="Fix auth issue")
            task.current_agent = "antigravity"
            storage.save_task(task)

            # Agent A produces structured result with recommendation
            agent_a_result = AgentResult(
                status=AgentResultStatus.PARTIAL,
                summary="Encountered token validation bug",
                error="Token expired unexpectedly",
                files_changed=["auth.py"],
                recommendations=["Check JWT expiry logic", "Add refresh token flow"],
                next_action="review authentication implementation",
                confidence=0.85,
            )

            # Handoff to Claude (Reviewer)
            envelope = orchestrator.handoff(
                task_id=task.task_id,
                source_agent="antigravity",
                target_agent="claude",
                result=agent_a_result,
                reason="Review required before retry",
            )

            assert envelope.source_agent == "antigravity"
            assert envelope.target_agent == "claude"
            assert envelope.result.files_changed == ["auth.py"]

            # Check task state updated
            updated_task = storage.get_task(task.task_id)
            assert updated_task is not None
            assert updated_task.current_agent == "claude"
            assert "antigravity" in updated_task.assigned_agents
            assert "claude" in updated_task.assigned_agents
            assert updated_task.next_action == "review authentication implementation"
            assert "Check JWT expiry logic" in updated_task.recommendations
            assert len(handoff_events) == 1
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_12_result_normalization_and_serialization(self):
        """Verify AgentResult JSON serialization, roundtripping, and confidence bounds."""
        res = AgentResult(
            result={"key": "value"},
            summary="Processed 10 records",
            error=None,
            files_changed=["data.json"],
            commands_run=["git status"],
            recommendations=["Verify integrity"],
            confidence=1.5,  # Exceeds 1.0; should clamp to 1.0
            status=AgentResultStatus.SUCCESS,
        )
        assert res.confidence == 1.0
        assert res.is_success is True
        assert res.has_error is False

        # JSON Roundtrip
        raw_json = res.to_json()
        reconstructed = AgentResult.from_json(raw_json)
        assert reconstructed.summary == res.summary
        assert reconstructed.files_changed == ["data.json"]
        assert reconstructed.status == AgentResultStatus.SUCCESS
        assert reconstructed.confidence == 1.0

    def test_13_event_bus_emission_and_wildcard_subscription(self):
        """Verify EventBus pattern matching, event replay, and error isolation."""
        bus = EventBus()
        all_events = []
        started_events = []

        bus.subscribe("*", lambda e: all_events.append(e))
        bus.subscribe("task.*", lambda e: None)  # Wildcard test
        bus.subscribe("task.started", lambda e: started_events.append(e))

        # Faulty handler should not crash bus
        def failing_handler(e):
            raise RuntimeError("Boom")
        bus.subscribe("task.started", failing_handler)

        bus.emit("task.created", task_id="t1")
        bus.emit("task.started", task_id="t1", agent_name="antigravity")

        assert len(all_events) == 2
        assert len(started_events) == 1
        assert started_events[0].agent_name == "antigravity"

        history = bus.get_history(task_id="t1")
        assert len(history) == 2

    def test_14_permissions_foundation_and_gating(self):
        """Verify high-risk permissions without human approval block execution."""
        task = AgentTask(
            goal="Delete system files",
            permissions=[TaskPermission.EXECUTE_COMMAND],
            requires_approval=True,
            approval_state="NONE",
        )
        agent = AgentMetadata(
            name="system_agent",
            provider="local",
            permissions=[TaskPermission.EXECUTE_COMMAND],
        )

        authorized, violations = PermissionPolicy.evaluate(task, agent)
        assert authorized is False
        assert any("human approval" in v for v in violations)

        # After approval
        task.approval_state = "APPROVED"
        auth_approved, violations_approved = PermissionPolicy.evaluate(task, agent)
        assert auth_approved is True
        assert len(violations_approved) == 0

    def test_15_concurrency_control_resource_governor(self):
        """Verify strict local worker concurrency ceiling (max_local_agents = 1)."""
        concurrency = ConcurrencyController(max_local_agents=1)

        # First worker acquires permit
        assert concurrency.acquire_local() is True
        assert concurrency.active_local_count == 1

        # Second worker is rejected due to 2-core CPU protection
        assert concurrency.acquire_local() is False

        # Release first worker
        concurrency.release_local()
        assert concurrency.active_local_count == 0

        # Now permit is available again
        assert concurrency.acquire_local() is True
        concurrency.release_local()

    def test_16_deterministic_task_verifier_separate_from_self_report(self):
        """Verify TaskVerifier checks independent evidence rather than trusting agent claim."""
        verifier = TaskVerifier()
        task = AgentTask(goal="Generate configuration", context={"min_confidence": 0.80})

        # Claim 1: Agent reports SUCCESS but empty output -> REJECT
        empty_res = AgentResult(status=AgentResultStatus.SUCCESS, summary="", confidence=0.90)
        v1 = verifier.verify(task, empty_res)
        assert v1.passed is False
        assert any("empty" in f for f in v1.failures)

        # Claim 2: Agent reports SUCCESS with error -> REJECT
        err_res = AgentResult(
            status=AgentResultStatus.SUCCESS,
            summary="All good",
            error="Silent failure occurred",
            confidence=0.90,
        )
        v2 = verifier.verify(task, err_res)
        assert v2.passed is False
        assert any("error" in f.lower() for f in v2.failures)

        # Claim 3: Agent reports SUCCESS with low confidence -> REJECT
        low_conf = AgentResult(status=AgentResultStatus.SUCCESS, summary="Maybe done", confidence=0.60)
        v3 = verifier.verify(task, low_conf)
        assert v3.passed is False
        assert any("threshold" in f.lower() for f in v3.failures)

        # Claim 4: Substantive valid result with high confidence -> PASS
        valid_res = AgentResult(
            status=AgentResultStatus.SUCCESS,
            summary="Configuration generated successfully",
            result={"settings": "ok"},
            confidence=0.95,
        )
        v4 = verifier.verify(task, valid_res)
        assert v4.passed is True
        assert len(v4.failures) == 0

    def test_17_antigravity_adapter_integration(self):
        """Verify AntigravityAdapter capabilities, health inspection, and task lifecycle."""
        adapter = AntigravityAdapter()
        caps = adapter.capabilities()
        assert "coding" in caps
        assert "debugging" in caps
        assert "artifact_generation" in caps

        health = adapter.health_check()
        assert health["provider"] == "antigravity"
        assert "status" in health

        task = AgentTask(goal="Refactor helper module")
        assert adapter.submit_task(task) is True
        assert adapter.get_task_status(task.task_id) == TaskStatus.RUNNING

        # Stream output
        output_chunks = list(adapter.stream_agent_output(task.task_id))
        assert len(output_chunks) > 0

        # Complete task
        res = AgentResult(
            status=AgentResultStatus.SUCCESS,
            summary="Helper refactored",
            files_changed=["helper.py"],
            confidence=0.98,
        )
        adapter.complete_task(task.task_id, res)
        assert adapter.get_task_status(task.task_id) == TaskStatus.COMPLETED
        assert adapter.get_task_result(task.task_id) == res

    def test_18_orchestrator_execute_task_success_lifecycle(self):
        """Verify orchestrator full execution with adapter and verification pass."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            registry = AgentRegistry(seed_defaults=False)
            mock_adapter = MockTestAdapter("fast_agent")
            mock_adapter.result_to_return = AgentResult(
                status=AgentResultStatus.SUCCESS,
                summary="Work completed with evidence",
                confidence=0.95,
            )
            registry.register_agent(
                AgentMetadata(name="fast_agent", provider="test", permissions=[]),
                adapter=mock_adapter,
            )

            orchestrator = JevOrchestrator(storage=storage, registry=registry)
            task = orchestrator.create_task(goal="Run quick work")
            task.current_agent = "fast_agent"

            result = orchestrator.execute_task(task)
            assert result.is_success is True

            updated = storage.get_task(task.task_id)
            assert updated is not None
            assert updated.status == TaskStatus.COMPLETED
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_19_orchestrator_execute_task_verification_failure(self):
        """Verify orchestrator transitions task to FAILED if independent verification fails."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            registry = AgentRegistry(seed_defaults=False)
            mock_adapter = MockTestAdapter("flaky_agent")
            # Result claims success but provides empty output -> verifier rejects
            mock_adapter.result_to_return = AgentResult(
                status=AgentResultStatus.SUCCESS,
                summary="",
                confidence=0.95,
            )
            registry.register_agent(
                AgentMetadata(name="flaky_agent", provider="test", permissions=[]),
                adapter=mock_adapter,
            )

            orchestrator = JevOrchestrator(storage=storage, registry=registry)
            task = orchestrator.create_task(goal="Flaky task")
            task.current_agent = "flaky_agent"

            result = orchestrator.execute_task(task)

            updated = storage.get_task(task.task_id)
            assert updated is not None
            assert updated.status == TaskStatus.FAILED
            assert len(updated.errors) > 0
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

    def test_20_orchestrator_execute_task_permission_blocked(self):
        """Verify orchestrator blocks execution if sensitive permission is unapproved."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            temp_db = f.name

        try:
            storage = TaskStorage(db_path=temp_db)
            registry = AgentRegistry(seed_defaults=False)
            mock_adapter = MockTestAdapter("sys_agent")
            registry.register_agent(
                AgentMetadata(
                    name="sys_agent",
                    provider="test",
                    permissions=[TaskPermission.CHANGE_SETTINGS],
                ),
                adapter=mock_adapter,
            )

            orchestrator = JevOrchestrator(storage=storage, registry=registry)
            task = orchestrator.create_task(
                goal="Change system configuration",
                permissions=[TaskPermission.CHANGE_SETTINGS],
                requires_approval=True,
            )
            task.current_agent = "sys_agent"

            result = orchestrator.execute_task(task)
            assert result.status == AgentResultStatus.BLOCKED

            updated = storage.get_task(task.task_id)
            assert updated is not None
            assert updated.status == TaskStatus.BLOCKED
        finally:
            if os.path.exists(temp_db):
                try:
                    os.remove(temp_db)
                except Exception:
                    pass

