"""Phase 14: Jev Multi-Agent Orchestration and Agent Collaboration Test Suite.

Tests:
1. AgentMessage universal envelope, types, and serialization
2. ContextFilterMode (FULL, RELEVANT, SUMMARY, ARTIFACTS_ONLY)
3. ArtifactReference tracking and serialization
4. AgentCapabilityCard formal specification and normalization
5. AgentRegistry capability card methods and auto-conversion
6. CapabilityScorer multi-factor scoring (cost, latency, health, perms)
7. AgentRouterV2 deterministic rule matching and fallback generation
8. AgentRouterV2 capability-based routing when rules do not match
9. Task ownership semantics (owner_agent, current_agent, delegated_agents)
10. Specialist-as-a-tool pattern (manager retains ownership)
11. Closed-loop Reviewer pattern: immediate approval
12. Closed-loop Reviewer pattern: 2 bounded rounds with feedback cycle
13. DeterministicAdjudicator contradiction detection and evidence weighting
14. SynthesisEngine and debate_goal resolution
15. Failure classification and automatic fallback routing
16. Mission lifecycle state transitions and persistence
17. Mission pause, resume, cancel, and voice interrupt
18. Mission timeline observability and credential sanitization ([REDACTED])
19. Concurrency governor strictly enforcing max_local_agents = 1
20. Demo 1: Autonomous Code Fix + Reviewer Loop
21. Demo 2: Research-to-Implementation Pipeline with Context Filtering
22. Demo 3: Multi-Specialist Mission Coordination with Observable Timeline
"""
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import pytest

from agent_capability import AgentCapabilityCard
from agent_message import AgentMessage, AgentMessageType, ArtifactReference, ContextFilterMode
from agent_registry import AgentMetadata, AgentRegistry
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPermission, TaskStatus
from mission import Mission, MissionStatus, TimelineEntry
from orchestrator import JevOrchestrator
from provider_mocks import (
    FailureType,
    MockCodingAgent,
    MockFailingAgent,
    MockResearchAgent,
    MockReviewerAgent,
    MockSlowAgent,
)
from reviewer_pattern import ReviewFeedback, ReviewLoopController, ReviewStatus
from router_v2 import AgentRouterV2, CapabilityScorer, RouterDecisionV2
from synthesis_engine import DeterministicAdjudicator, SynthesisEngine, SynthesizedResolution
from task_storage import TaskStorage


class TestPhase14MultiAgentOrchestration:
    """Comprehensive test suite for Phase 14 multi-agent orchestration."""

    def setup_method(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db_path = self.temp_db.name
        self.temp_db.close()
        self.storage = TaskStorage(db_path=self.temp_db_path)
        self.registry = AgentRegistry(seed_defaults=True)
        self.orchestrator = JevOrchestrator(storage=self.storage, registry=self.registry)

    def teardown_method(self) -> None:
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    # -------------------------------------------------------------------------
    # 1. AgentMessage Envelope & Serialization
    # -------------------------------------------------------------------------
    def test_01_agent_message_envelope_and_serialization(self) -> None:
        msg = AgentMessage(
            task_id="task_100",
            sender_agent="jev_orchestrator",
            receiver_agent="coding_agent",
            message_type=AgentMessageType.TASK_REQUEST,
            goal="Refactor auth parser",
            instructions="Ensure RFC 6750 compliance",
            inputs={"framework": "fastapi"},
            permissions=["READ_FILES", "WRITE_FILES"],
        )
        d = msg.to_dict()
        assert d["task_id"] == "task_100"
        assert d["sender_agent"] == "jev_orchestrator"
        assert d["receiver_agent"] == "coding_agent"
        assert d["message_type"] == "TASK_REQUEST"
        assert d["instructions"] == "Ensure RFC 6750 compliance"

        restored = AgentMessage.from_dict(d)
        assert restored.message_id == msg.message_id
        assert restored.message_type == AgentMessageType.TASK_REQUEST

        reply = msg.create_reply(
            sender_agent="coding_agent",
            message_type=AgentMessageType.TASK_RESULT,
            result=AgentResult(summary="Code updated", status=AgentResultStatus.SUCCESS),
        )
        assert reply.reply_to == msg.message_id
        assert reply.receiver_agent == "jev_orchestrator"
        assert reply.message_type == AgentMessageType.TASK_RESULT

    # -------------------------------------------------------------------------
    # 2. Context Filtering Modes
    # -------------------------------------------------------------------------
    def test_02_context_filtering_modes(self) -> None:
        raw_ctx = {
            "goal": "Fix SQL query latency",
            "error": "Query timeout after 30s",
            "file_path": "db/queries.py",
            "huge_chat_log": ["msg1", "msg2"] * 500,
            "internal_tokens": [1, 2, 3] * 1000,
            "summary": "Database performance bottleneck",
        }
        # Mode FULL
        full = AgentMessage.filter_context_dict(raw_ctx, ContextFilterMode.FULL)
        assert "huge_chat_log" in full
        assert "internal_tokens" in full

        # Mode RELEVANT
        relevant = AgentMessage.filter_context_dict(raw_ctx, ContextFilterMode.RELEVANT)
        assert "goal" in relevant
        assert "error" in relevant
        assert "file_path" in relevant
        assert "huge_chat_log" not in relevant
        assert "internal_tokens" not in relevant

        # Mode SUMMARY
        summary = AgentMessage.filter_context_dict(raw_ctx, ContextFilterMode.SUMMARY)
        assert "summary" in summary
        assert summary["summary"] == "Database performance bottleneck"
        assert "file_path" not in summary

        # Mode ARTIFACTS_ONLY
        msg = AgentMessage(
            artifacts=[{"artifact_id": "art_1", "type": "file", "path": "test.py"}],
            context=raw_ctx,
        )
        art_only = msg.filter_context(ContextFilterMode.ARTIFACTS_ONLY)
        assert "artifacts" in art_only
        assert len(art_only["artifacts"]) == 1

    # -------------------------------------------------------------------------
    # 3. Artifact Reference Tracking
    # -------------------------------------------------------------------------
    def test_03_artifact_references_and_checksums(self) -> None:
        art = ArtifactReference(
            type="file",
            uri_or_path="C:/test/auth.py",
            producer_agent="antigravity",
            task_id="task_200",
            checksum="a1b2c3d4",
            metadata={"lines_changed": 14},
        )
        d = art.to_dict()
        assert d["producer_agent"] == "antigravity"
        assert d["checksum"] == "a1b2c3d4"

        restored = ArtifactReference.from_dict(d)
        assert restored.artifact_id == art.artifact_id
        assert restored.metadata["lines_changed"] == 14

    # -------------------------------------------------------------------------
    # 4. Agent Capability Card Model & Normalization
    # -------------------------------------------------------------------------
    def test_04_agent_capability_card_model_and_normalization(self) -> None:
        card = AgentCapabilityCard(
            agent_id="code_expert",
            name="code_expert",
            provider="google",
            capabilities=["coding", "debugging", "ast_analysis"],
            permissions=["READ_FILES", "WRITE_FILES"],  # type: ignore
            cost_class="low",
            latency_class="fast",
            reliability=0.98,
            supports_a2a=True,
            supports_mcp=True,
        )
        assert isinstance(card.permissions[0], TaskPermission)
        assert card.permissions[0] == TaskPermission.READ_FILES

        d = card.to_dict()
        assert d["permissions"] == ["READ_FILES", "WRITE_FILES"]
        assert d["supports_a2a"] is True

        restored = AgentCapabilityCard.from_dict(d)
        assert restored.name == "code_expert"
        assert restored.permissions[0] == TaskPermission.READ_FILES

    # -------------------------------------------------------------------------
    # 5. Registry Capability Card Integration
    # -------------------------------------------------------------------------
    def test_05_registry_capability_card_integration(self) -> None:
        card = AgentCapabilityCard(
            agent_id="card_agent",
            name="card_agent",
            provider="test_provider",
            capabilities=["specialist_task"],
            permissions=[TaskPermission.READ_FILES],
            cost_class="free",
            latency_class="fast",
        )
        self.registry.register_card(card)

        fetched = self.registry.get_card("card_agent")
        assert fetched is not None
        assert fetched.name == "card_agent"
        assert fetched.cost_class == "free"

        # Check that legacy agent also returns an auto-generated capability card
        antigravity_card = self.registry.get_card("antigravity")
        assert antigravity_card is not None
        assert "coding" in antigravity_card.capabilities

        all_cards = self.registry.list_cards()
        assert len(all_cards) >= 4

    # -------------------------------------------------------------------------
    # 6. CapabilityScorer Multi-Factor Scoring
    # -------------------------------------------------------------------------
    def test_06_capability_scorer_multi_factor_weights(self) -> None:
        scorer = CapabilityScorer()
        task = AgentTask(goal="Fix broken unit test", permissions=[TaskPermission.READ_FILES])

        available_agent = AgentMetadata(
            name="agent_good",
            provider="test",
            capabilities=["coding", "tests", "debugging"],
            cost="low",
            speed="fast",
            reliability=0.95,
            availability="available",
            permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        )

        unhealthy_agent = AgentMetadata(
            name="agent_bad",
            provider="test",
            capabilities=["coding"],
            cost="high",
            speed="slow",
            reliability=0.40,
            availability="unavailable",
            health_status="unhealthy",
            permissions=[],
        )

        scored_good = scorer.score_candidate(task, available_agent, required_capabilities=["coding"])
        scored_bad = scorer.score_candidate(task, unhealthy_agent, required_capabilities=["coding"])

        assert scored_good.total_score > 0.80
        assert scored_bad.total_score == 0.0
        assert scored_good.total_score > scored_bad.total_score

    # -------------------------------------------------------------------------
    # 7. AgentRouterV2 Deterministic Rule Matches & Fallbacks
    # -------------------------------------------------------------------------
    def test_07_router_v2_deterministic_rule_matching(self) -> None:
        router = AgentRouterV2()

        # Coding task
        task_code = AgentTask(goal="Fix bug in token validation code")
        d1 = router.route(task_code, self.registry)
        assert d1.selected_agent == "antigravity"
        assert d1.strategy_used == "rule_matched"
        assert len(d1.fallback_agents) > 0

        # Review task
        task_review = AgentTask(goal="Review and evaluate architecture plan")
        d2 = router.route(task_review, self.registry)
        assert d2.selected_agent == "claude"
        assert d2.strategy_used == "rule_matched"

        # Browser task
        task_browser = AgentTask(goal="Navigate to website and open tab")
        d3 = router.route(task_browser, self.registry)
        assert d3.selected_agent == "browser_agent"

        # Research task
        task_research = AgentTask(goal="Research beachfront hotels in Australia")
        d4 = router.route(task_research, self.registry)
        assert d4.selected_agent == "research_agent"

    # -------------------------------------------------------------------------
    # 8. AgentRouterV2 Capability Scoring when Rules Do Not Match
    # -------------------------------------------------------------------------
    def test_08_router_v2_capability_scoring_fallback(self) -> None:
        router = AgentRouterV2()
        custom_task = AgentTask(goal="Examine system telemetry data metrics")
        decision = router.route(custom_task, self.registry)
        assert decision.selected_agent is not None
        assert len(decision.scored_candidates) > 0

    # -------------------------------------------------------------------------
    # 9. Task Ownership and Delegation Semantics
    # -------------------------------------------------------------------------
    def test_09_task_ownership_and_delegation_semantics(self) -> None:
        task = self.orchestrator.create_task(
            goal="Manage project setup",
            owner_agent="jev_orchestrator",
        )
        assert task.owner_agent == "jev_orchestrator"
        assert task.current_agent is None
        assert task.delegated_agents == []

        # Delegate subtask
        task.current_agent = "antigravity"
        task.delegated_agents.append("research_agent")
        self.storage.save_task(task)

        loaded = self.storage.get_task(task.task_id)
        assert loaded is not None
        assert loaded.owner_agent == "jev_orchestrator"
        assert loaded.current_agent == "antigravity"
        assert "research_agent" in loaded.delegated_agents

    # -------------------------------------------------------------------------
    # 10. Specialist-as-a-Tool Pattern
    # -------------------------------------------------------------------------
    def test_10_specialist_as_tool_pattern(self) -> None:
        research_adapter = MockResearchAgent()
        self.registry.register_agent(
            AgentMetadata(name="research_tool", provider="mock", capabilities=["research"]),
            adapter=research_adapter,
        )

        result = self.orchestrator.call_specialist_as_tool(
            calling_agent="coding_manager",
            specialist_agent="research_tool",
            goal="Lookup standard HTTP 429 backoff headers",
            input_data={"protocol": "http"},
        )

        assert result.is_success
        assert "research" in result.summary.lower()

        # Verify manager remained separate and subtask was recorded
        subtasks = self.storage.get_tasks_by_status(TaskStatus.COMPLETED)
        tool_task = next((t for t in subtasks if t.goal == "Lookup standard HTTP 429 backoff headers"), None)
        assert tool_task is not None
        assert tool_task.owner_agent == "coding_manager"
        assert tool_task.current_agent == "research_tool"

    # -------------------------------------------------------------------------
    # 11. Closed-Loop Reviewer Pattern: Immediate Approval
    # -------------------------------------------------------------------------
    def test_11_closed_loop_reviewer_pattern_approval(self) -> None:
        coding_adapter = MockCodingAgent()
        reviewer_adapter = MockReviewerAgent()
        self.registry.register_agent(AgentMetadata(name="coder", provider="mock"), adapter=coding_adapter)
        self.registry.register_agent(AgentMetadata(name="reviewer", provider="mock"), adapter=reviewer_adapter)

        task = self.orchestrator.create_task(goal="Implement clean token check")
        status, history = self.orchestrator.request_review(
            task_id=task.task_id,
            worker_agent="coder",
            reviewer_agent="reviewer",
            max_rounds=2,
        )

        assert status == ReviewStatus.APPROVED
        assert len(history) == 1
        assert history[0].status == ReviewStatus.APPROVED

    # -------------------------------------------------------------------------
    # 12. Closed-Loop Reviewer Pattern: Bounded Two Rounds
    # -------------------------------------------------------------------------
    def test_12_closed_loop_reviewer_pattern_bounded_two_rounds(self) -> None:
        controller = ReviewLoopController(max_review_rounds=2)
        task = AgentTask(goal="Refactor authentication")

        def worker_fn(t: AgentTask, feedback: Optional[ReviewFeedback]) -> AgentResult:
            if feedback is None:
                # Round 1 initial draft: has flaws
                return AgentResult(
                    summary="Initial code draft",
                    error="Missing bearer validation",
                    status=AgentResultStatus.FAILURE,
                )
            else:
                # Round 2 addressed feedback
                return AgentResult(
                    summary="Updated draft with bearer validation",
                    status=AgentResultStatus.SUCCESS,
                    recommendations=["Validated according to reviewer suggestion"],
                )

        def reviewer_fn(t: AgentTask, res: AgentResult, round_num: int) -> ReviewFeedback:
            if round_num == 1:
                return ReviewFeedback(
                    status=ReviewStatus.CHANGES_REQUESTED,
                    issues=["Lacks Bearer check"],
                    summary="Changes requested: missing token prefix",
                    round_number=1,
                )
            return ReviewFeedback(
                status=ReviewStatus.APPROVED,
                summary="Approved after fixes",
                round_number=2,
            )

        final_res, history = controller.execute_review_round(task, "coder", "reviewer", worker_fn, reviewer_fn)

        assert final_res.is_success
        assert len(history) == 2
        assert history[0].status == ReviewStatus.CHANGES_REQUESTED
        assert history[1].status == ReviewStatus.APPROVED

    # -------------------------------------------------------------------------
    # 13. Deterministic Adjudicator Contradiction Detection
    # -------------------------------------------------------------------------
    def test_13_deterministic_adjudicator_contradiction_detection(self) -> None:
        adjudicator = DeterministicAdjudicator()

        positions = [
            {
                "agent_name": "agent_alpha",
                "recommendation": "Use local SQLite WAL mode",
                "action": "configure_sqlite",
                "confidence": 0.95,
                "evidence": ["Compatible with Windows 10", "Zero network overhead"],
            },
            {
                "agent_name": "agent_beta",
                "recommendation": "Use remote Redis cluster",
                "action": "configure_redis",
                "confidence": 0.85,
                "evidence": ["Fails on Windows without WSL"],
            },
        ]

        resolution = adjudicator.adjudicate(positions)
        assert resolution.selected_action == "configure_sqlite"
        assert resolution.winning_recommendation == "Use local SQLite WAL mode"
        assert len(resolution.contradictions_detected) == 1
        assert resolution.contradictions_detected[0]["agent_a"] == "agent_alpha"
        assert resolution.contradictions_detected[0]["agent_b"] == "agent_beta"
        assert len(resolution.accepted_positions) == 1
        assert len(resolution.rejected_positions) == 1

    # -------------------------------------------------------------------------
    # 14. SynthesisEngine & debate_goal Resolution
    # -------------------------------------------------------------------------
    def test_14_synthesis_engine_multi_opinion_debate(self) -> None:
        opinions = {
            "antigravity": {
                "recommendation": "Implement token cache with 5 minute TTL",
                "action": "add_cache",
                "confidence": 0.92,
                "evidence": ["Benchmarked at 0.05ms lookup"],
            },
            "claude": {
                "recommendation": "Implement token cache with 1 minute TTL",
                "action": "add_short_cache",
                "confidence": 0.88,
                "evidence": ["Mitigates stale revocation"],
            },
        }

        resolution = self.orchestrator.debate_goal("Token cache TTL decision", opinions)
        assert resolution.selected_action in ("add_cache", "add_short_cache")
        assert resolution.confidence > 0.8
        assert len(resolution.contradictions_detected) == 1

    # -------------------------------------------------------------------------
    # 15. Failure Classification & Automatic Fallback Routing
    # -------------------------------------------------------------------------
    def test_15_failure_classification_and_automatic_fallback(self) -> None:
        failing_adapter = MockFailingAgent(failure_type=FailureType.RATE_LIMIT)
        working_adapter = MockCodingAgent()

        self.registry.register_agent(
            AgentMetadata(name="primary_worker", provider="mock", capabilities=["coding"]),
            adapter=failing_adapter,
        )
        self.registry.register_agent(
            AgentMetadata(name="fallback_worker", provider="mock", capabilities=["coding"]),
            adapter=working_adapter,
        )

        task = self.orchestrator.create_task(
            goal="Implement fix with fallback support",
            context={"fallback_agents": ["fallback_worker"]},
        )
        task.current_agent = "primary_worker"

        # Execute task: primary fails, orchestrator automatically falls back to fallback_worker
        result = self.orchestrator.execute_task(task, auto_fallback=True)

        assert result.is_success
        assert task.current_agent == "fallback_worker"
        assert "fallback_worker" in task.assigned_agents
        assert "primary_worker" in task.assigned_agents

        # Audit trail must verify the fallback event
        audits = self.storage.get_audit_trail(task.task_id)
        fallback_events = [a for a in audits if a["event_type"] == "task.fallback"]
        assert len(fallback_events) == 1
        assert fallback_events[0]["agent_name"] == "fallback_worker"

    # -------------------------------------------------------------------------
    # 16. Mission Lifecycle State Transitions & Persistence
    # -------------------------------------------------------------------------
    def test_16_mission_lifecycle_state_machine(self) -> None:
        mission = self.orchestrator.create_mission(
            title="Refactor Authentication Engine",
            goal="Standardize tokens and add review gate",
        )
        assert mission.status == MissionStatus.CREATED

        mission.transition_to(MissionStatus.PLANNING, reason="Decomposing tasks")
        assert mission.status == MissionStatus.PLANNING

        mission.transition_to(MissionStatus.EXECUTING, reason="Executing tasks")
        assert mission.status == MissionStatus.EXECUTING

        self.orchestrator.storage.save_mission(mission)
        loaded = self.orchestrator.get_mission(mission.mission_id)
        assert loaded is not None
        assert loaded.status == MissionStatus.EXECUTING
        assert len(loaded.timeline) >= 3

    # -------------------------------------------------------------------------
    # 17. Mission Pause, Resume, Cancel, and Voice Interrupt
    # -------------------------------------------------------------------------
    def test_17_mission_pause_resume_cancel_and_voice_interrupt(self) -> None:
        mission = self.orchestrator.create_mission(
            title="Interactive Long Mission",
            goal="Collect and analyze network diagnostics",
        )

        # Pause
        assert self.orchestrator.pause_mission(mission.mission_id, reason="User interrupted")
        m_paused = self.orchestrator.get_mission(mission.mission_id)
        assert m_paused is not None
        assert m_paused.status == MissionStatus.PAUSED

        # Resume
        assert self.orchestrator.resume_mission(mission.mission_id)
        m_resumed = self.orchestrator.get_mission(mission.mission_id)
        assert m_resumed is not None
        assert m_resumed.status == MissionStatus.EXECUTING

        # Voice interrupt
        assert self.orchestrator.interrupt_mission(mission.mission_id, reason="Wake word heard")
        m_interrupted = self.orchestrator.get_mission(mission.mission_id)
        assert m_interrupted is not None
        assert m_interrupted.status == MissionStatus.PAUSED

        # Cancel
        assert self.orchestrator.cancel_mission(mission.mission_id, reason="User cancelled")
        m_cancelled = self.orchestrator.get_mission(mission.mission_id)
        assert m_cancelled is not None
        assert m_cancelled.status == MissionStatus.CANCELLED

    # -------------------------------------------------------------------------
    # 18. Mission Timeline Observability & Credential Sanitization
    # -------------------------------------------------------------------------
    def test_18_mission_timeline_observability_and_secret_redaction(self) -> None:
        mission = self.orchestrator.create_mission(
            title="Security Audit Mission",
            goal="Inspect configuration files",
        )

        # Attempt to record sensitive details in timeline
        entry = mission.record_timeline(
            event_type="auth.check",
            summary="Checked API authentication",
            agent_name="antigravity",
            details={
                "service": "openai",
                "api_key": "sk-secret123456789",
                "auth_token": "bearer_abc987654",
                "normal_field": "ok_value",
            },
        )

        assert entry.details["normal_field"] == "ok_value"
        assert entry.details["api_key"] == "[REDACTED]"
        assert entry.details["auth_token"] == "[REDACTED]"

    # -------------------------------------------------------------------------
    # 19. Concurrency Governor Strictly Enforcing max_local_agents = 1
    # -------------------------------------------------------------------------
    def test_19_concurrency_governor_strict_enforcement(self) -> None:
        governor = self.orchestrator.concurrency
        assert governor.max_local_agents == 1

        # First slot acquired
        assert governor.acquire_local() is True
        assert governor.active_local_count == 1

        # Second slot refused to avoid starving voice loop
        assert governor.acquire_local() is False

        # Release restores slot
        governor.release_local()
        assert governor.active_local_count == 0
        assert governor.acquire_local() is True
        governor.release_local()

    # -------------------------------------------------------------------------
    # 20. Demo 1: Autonomous Code Fix + Reviewer Loop
    # -------------------------------------------------------------------------
    def test_20_demo_01_autonomous_code_fix_and_reviewer_loop(self) -> None:
        coding_adapter = MockCodingAgent()
        reviewer_adapter = MockReviewerAgent()

        self.registry.register_agent(
            AgentMetadata(
                name="demo_coder",
                provider="antigravity",
                capabilities=["coding", "debugging", "tests"],
                permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            ),
            adapter=coding_adapter,
        )
        self.registry.register_agent(
            AgentMetadata(
                name="demo_reviewer",
                provider="claude",
                capabilities=["review", "evaluation"],
            ),
            adapter=reviewer_adapter,
        )

        # 1. Orchestrator creates task to fix bug
        task = self.orchestrator.create_task(
            goal="Fix authentication token parser bug in auth_service.py",
            permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
        )

        # 2. Worker executes implementation
        task.current_agent = "demo_coder"
        code_result = self.orchestrator.execute_task(task)
        assert code_result.is_success
        assert "auth_service.py" in code_result.files_changed

        # 3. Independent Reviewer conducts quality gate review
        review_status, review_history = self.orchestrator.request_review(
            task_id=task.task_id,
            worker_agent="demo_coder",
            reviewer_agent="demo_reviewer",
            max_rounds=2,
        )

        assert review_status == ReviewStatus.APPROVED
        assert len(review_history) >= 1
        assert "RFC 6750" in review_history[0].summary

    # -------------------------------------------------------------------------
    # 21. Demo 2: Research-to-Implementation Pipeline
    # -------------------------------------------------------------------------
    def test_21_demo_02_research_to_implementation_pipeline(self) -> None:
        research_adapter = MockResearchAgent()
        coding_adapter = MockCodingAgent()

        self.registry.register_agent(
            AgentMetadata(name="research_worker", provider="web", capabilities=["research"]),
            adapter=research_adapter,
        )
        self.registry.register_agent(
            AgentMetadata(name="code_worker", provider="google", capabilities=["coding"]),
            adapter=coding_adapter,
        )

        # 1. Research phase
        task = self.orchestrator.create_task(
            goal="Research RFC 6750 token format requirements",
            context={"verbose_web_html": "<html><body>Unfiltered raw content</body></html>" * 200},
        )
        task.current_agent = "research_worker"
        r_result = self.orchestrator.execute_task(task)
        assert r_result.is_success

        # 2. Handoff to Coding worker with RELEVANT context filtering
        envelope = self.orchestrator.handoff_to_agent(
            task_id=task.task_id,
            source_agent="research_worker",
            target_agent="code_worker",
            reason="Research complete; implement header formatting",
            context_filter=ContextFilterMode.RELEVANT,
        )

        assert envelope.target_agent == "code_worker"
        assert "verbose_web_html" not in envelope.context

        # Reload updated task from storage
        updated_task = self.orchestrator.storage.get_task(task.task_id)
        assert updated_task is not None
        assert updated_task.current_agent == "code_worker"
        assert updated_task.status == TaskStatus.QUEUED

        # 3. Implementation phase executes with filtered context
        c_result = self.orchestrator.execute_task(updated_task)
        assert c_result.is_success

    # -------------------------------------------------------------------------
    # 22. Demo 3: Multi-Specialist Mission Coordination
    # -------------------------------------------------------------------------
    def test_22_demo_03_multi_specialist_mission_coordination(self) -> None:
        research_adapter = MockResearchAgent()
        coding_adapter = MockCodingAgent()
        reviewer_adapter = MockReviewerAgent()

        self.registry.register_agent(
            AgentMetadata(name="spec_researcher", provider="web", capabilities=["research"]),
            adapter=research_adapter,
        )
        self.registry.register_agent(
            AgentMetadata(name="spec_coder", provider="google", capabilities=["coding"]),
            adapter=coding_adapter,
        )
        self.registry.register_agent(
            AgentMetadata(name="spec_reviewer", provider="anthropic", capabilities=["review"]),
            adapter=reviewer_adapter,
        )

        # Create high-level mission
        mission = self.orchestrator.create_mission(
            title="Complete Auth Upgrade Mission",
            goal="Research standards, implement token fix, and verify via code review",
        )

        # Specialist 1: Research Tool Call
        r_res = self.orchestrator.call_specialist_as_tool(
            calling_agent="jev_orchestrator",
            specialist_agent="spec_researcher",
            goal="Lookup Bearer authorization prefix specs",
        )
        assert r_res.is_success
        mission.record_timeline("mission.research_done", "Research complete", agent_name="spec_researcher")

        # Specialist 2: Code Implementation Task
        c_task = self.orchestrator.create_task(
            goal="Apply RFC 6750 header prefix",
            context={"evidence": r_res.evidence},
        )
        mission.add_task(c_task.task_id)
        c_task.current_agent = "spec_coder"
        c_res = self.orchestrator.execute_task(c_task)
        assert c_res.is_success
        mission.record_timeline("mission.code_done", "Code implemented", agent_name="spec_coder")

        # Specialist 3: Peer Review Gate
        rev_status, _ = self.orchestrator.request_review(
            task_id=c_task.task_id,
            worker_agent="spec_coder",
            reviewer_agent="spec_reviewer",
            max_rounds=2,
        )
        assert rev_status == ReviewStatus.APPROVED
        mission.record_timeline("mission.review_approved", "Changes verified and approved", agent_name="spec_reviewer")

        # Complete mission
        mission.transition_to(MissionStatus.COMPLETED, reason="All 3 specialists completed objectives")
        self.orchestrator.storage.save_mission(mission)

        # Verify persisted mission timeline
        loaded = self.orchestrator.get_mission(mission.mission_id)
        assert loaded is not None
        assert loaded.status == MissionStatus.COMPLETED
        assert len(loaded.timeline) >= 4
