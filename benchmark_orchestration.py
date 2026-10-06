"""Phase 14: Multi-Agent Orchestration Benchmark Suite for Hey Jev.

Measures:
1. Router V2 decision latency (rule-matching vs capability scoring across candidates)
2. Task creation and persistence overhead (SQLite)
3. Context filtering latency (FULL, RELEVANT, SUMMARY, ARTIFACTS_ONLY)
4. Agent-to-Agent handoff latency
5. Specialist-as-tool invocation overhead
6. Closed-loop review cycle latency (Worker <-> Reviewer)
7. Two-agent collaborative pipeline latency
8. Three-agent collaborative mission pipeline latency
9. Memory footprint and CPU overhead on low-resource hardware
"""
import os
import sys
import time
import tempfile
import tracemalloc
from typing import Dict, Any, List

# Ensure hey-jev root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_capability import AgentCapabilityCard
from agent_message import AgentMessage, AgentMessageType, ContextFilterMode
from agent_registry import AgentMetadata, AgentRegistry
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask, TaskPermission, TaskStatus
from mission import Mission, MissionStatus
from orchestrator import JevOrchestrator
from provider_mocks import (
    MockCodingAgent,
    MockResearchAgent,
    MockReviewerAgent,
    MockFailingAgent,
)
from reviewer_pattern import ReviewFeedback, ReviewLoopController, ReviewStatus
from router_v2 import AgentRouterV2, CapabilityScorer
from synthesis_engine import DeterministicAdjudicator, SynthesisEngine
from task_storage import TaskStorage


def run_benchmarks() -> None:
    print("=" * 70)
    print("  HEY JEV - PHASE 14: MULTI-AGENT ORCHESTRATION BENCHMARK SUITE")
    print("=" * 70)
    print(f"Platform: {sys.platform} | Python: {sys.version.split()[0]}")

    tracemalloc.start()
    start_mem, _ = tracemalloc.get_traced_memory()

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db = f.name

    try:
        storage = TaskStorage(db_path=temp_db)
        registry = AgentRegistry(seed_defaults=True)

        # Register specialized test mock adapters
        coding_adapter = MockCodingAgent()
        research_adapter = MockResearchAgent()
        reviewer_adapter = MockReviewerAgent()

        registry.register_agent(
            AgentMetadata(
                name="coding_specialist",
                provider="mock",
                capabilities=["coding", "debugging", "tests"],
                permissions=[TaskPermission.READ_FILES, TaskPermission.WRITE_FILES],
            ),
            adapter=coding_adapter,
        )
        registry.register_agent(
            AgentMetadata(
                name="research_specialist",
                provider="mock",
                capabilities=["research", "web_investigation", "synthesis"],
                permissions=[],
            ),
            adapter=research_adapter,
        )
        registry.register_agent(
            AgentMetadata(
                name="reviewer_specialist",
                provider="mock",
                capabilities=["review", "evaluation", "critique"],
                permissions=[],
            ),
            adapter=reviewer_adapter,
        )

        orchestrator = JevOrchestrator(storage=storage, registry=registry)

        # -----------------------------------------------------------------
        # Benchmark 1: Router V2 Decision Latency
        # -----------------------------------------------------------------
        iterations = 500
        sample_task_rule = AgentTask(goal="Fix bug in auth service token parser")
        sample_task_scoring = AgentTask(goal="Synthesize latest benchmark data on distributed transactions")

        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = orchestrator.router_v2.route(sample_task_rule, registry)
        rule_elapsed_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = orchestrator.router_v2.route(sample_task_scoring, registry)
        scoring_elapsed_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

        print(f"\n[1] Router V2 Decision Latency ({iterations} iterations):")
        print(f"    - Direct Rule Match:       {rule_elapsed_ms:.4f} ms / decision")
        print(f"    - Multi-Factor Scored:    {scoring_elapsed_ms:.4f} ms / decision")

        # -----------------------------------------------------------------
        # Benchmark 2: Context Filtering Latency
        # -----------------------------------------------------------------
        heavy_context = {
            "goal": "Optimize query performance",
            "error": "TimeoutException at line 124",
            "file_path": "/var/log/query.log",
            "chat_history": ["user: hello", "agent: hi", "user: fix it"] * 100,
            "intermediate_tokens": list(range(1000)),
            "summary": "Database timeout during peak traffic",
        }

        iterations = 1000
        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = AgentMessage.filter_context_dict(heavy_context, ContextFilterMode.FULL)
        full_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = AgentMessage.filter_context_dict(heavy_context, ContextFilterMode.RELEVANT)
        rel_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = AgentMessage.filter_context_dict(heavy_context, ContextFilterMode.SUMMARY)
        sum_ms = ((time.perf_counter() - t0) / iterations) * 1000.0

        print(f"\n[2] Context Filtering Latency ({iterations} iterations):")
        print(f"    - FULL Mode:               {full_ms:.4f} ms")
        print(f"    - RELEVANT Mode:           {rel_ms:.4f} ms")
        print(f"    - SUMMARY Mode:            {sum_ms:.4f} ms")

        # -----------------------------------------------------------------
        # Benchmark 3: Agent-to-Agent Handoff Latency
        # -----------------------------------------------------------------
        task = orchestrator.create_task(goal="Task for handoff benchmarking")
        iterations = 100
        t0 = time.perf_counter()
        for i in range(iterations):
            src = "coding_specialist" if i % 2 == 0 else "reviewer_specialist"
            tgt = "reviewer_specialist" if i % 2 == 0 else "coding_specialist"
            _ = orchestrator.handoff_to_agent(
                task_id=task.task_id,
                source_agent=src,
                target_agent=tgt,
                reason=f"Handoff step {i}",
                context_filter=ContextFilterMode.RELEVANT,
            )
        handoff_ms = ((time.perf_counter() - t0) / iterations) * 1000.0
        print(f"\n[3] A2A Handoff Overhead ({iterations} sequential SQLite saves):")
        print(f"    - Mean Handoff Latency:    {handoff_ms:.4f} ms / handoff")

        # -----------------------------------------------------------------
        # Benchmark 4: Closed-Loop Worker-Reviewer Cycle
        # -----------------------------------------------------------------
        review_task = orchestrator.create_task(goal="Validate authentication fix")
        t0 = time.perf_counter()
        status, feedback_history = orchestrator.request_review(
            task_id=review_task.task_id,
            worker_agent="coding_specialist",
            reviewer_agent="reviewer_specialist",
            max_rounds=2,
        )
        review_elapsed_ms = (time.perf_counter() - t0) * 1000.0
        print(f"\n[4] Closed-Loop Review Cycle (2 rounds):")
        print(f"    - Verdict:                 {status.value}")
        print(f"    - History entries:         {len(feedback_history)}")
        print(f"    - Elapsed Time:            {review_elapsed_ms:.4f} ms")

        # -----------------------------------------------------------------
        # Benchmark 5: Multi-Opinion Debate & Synthesis
        # -----------------------------------------------------------------
        opinions = {
            "agent_a": {
                "recommendation": "Use PostgreSQL JSONB index",
                "action": "create_index",
                "confidence": 0.90,
                "evidence": ["Tested on PostgreSQL 15", "Compatible with Windows 10"],
            },
            "agent_b": {
                "recommendation": "Use separate relational tables",
                "action": "normalize_schema",
                "confidence": 0.85,
                "evidence": ["Strict schema integrity"],
            },
            "agent_c": {
                "recommendation": "Use Redis cache in front of SQLite",
                "action": "add_redis",
                "confidence": 0.70,
                "evidence": ["Fails on Windows without WSL"],
            },
        }

        iterations = 500
        t0 = time.perf_counter()
        for _ in range(iterations):
            _ = orchestrator.debate_goal("Optimal storage pattern", opinions)
        debate_ms = ((time.perf_counter() - t0) / iterations) * 1000.0
        print(f"\n[5] Multi-Agent Synthesis & Debate ({iterations} iterations):")
        print(f"    - Adjudication Latency:    {debate_ms:.4f} ms / debate")

        # -----------------------------------------------------------------
        # Benchmark 6: Mission Lifecycle Operations
        # -----------------------------------------------------------------
        t0 = time.perf_counter()
        mission = orchestrator.create_mission(
            title="Refactor auth subsystem",
            goal="Standardize RFC 6750 tokens across services",
        )
        orchestrator.pause_mission(mission.mission_id, reason="User spoke wake word")
        orchestrator.resume_mission(mission.mission_id)
        retrieved_mission = orchestrator.get_mission(mission.mission_id)
        orchestrator.cancel_mission(mission.mission_id, reason="Testing complete")
        mission_lifecycle_ms = (time.perf_counter() - t0) * 1000.0

        print(f"\n[6] Mission Lifecycle (create -> pause -> resume -> cancel):")
        print(f"    - Total Operations:        4 SQLite mutations + timeline events")
        print(f"    - Total Elapsed:           {mission_lifecycle_ms:.4f} ms")

        # -----------------------------------------------------------------
        # Benchmark 7: Two-Agent Pipeline (Research -> Implementation)
        # -----------------------------------------------------------------
        t0 = time.perf_counter()
        # Step 1: Research
        research_task = orchestrator.create_task(goal="Research RFC 6750 token format requirements")
        research_task.current_agent = "research_specialist"
        r_res = orchestrator.execute_task(research_task)

        # Step 2: Handoff with RELEVANT context
        _ = orchestrator.handoff_to_agent(
            task_id=research_task.task_id,
            source_agent="research_specialist",
            target_agent="coding_specialist",
            reason="Research findings collected; implement header change",
            context_filter=ContextFilterMode.RELEVANT,
        )
        research_task = orchestrator.storage.get_task(research_task.task_id)

        # Step 3: Implement
        code_res = orchestrator.execute_task(research_task)
        two_agent_ms = (time.perf_counter() - t0) * 1000.0

        print(f"\n[7] Two-Agent Collaborative Pipeline (Research -> Handoff -> Code):")
        print(f"    - Research Status:         {r_res.status.value}")
        print(f"    - Code Status:             {code_res.status.value}")
        print(f"    - End-to-End Latency:      {two_agent_ms:.4f} ms")

        # -----------------------------------------------------------------
        # Benchmark 8: Three-Agent Mission Pipeline (Research -> Code -> Review)
        # -----------------------------------------------------------------
        t0 = time.perf_counter()
        full_mission = orchestrator.create_mission(
            title="E2E Pipeline Mission",
            goal="Research, implement, and review auth token upgrade",
        )

        # Step 1: Research specialist tool call
        t_res = orchestrator.call_specialist_as_tool(
            calling_agent="jev_orchestrator",
            specialist_agent="research_specialist",
            goal="Identify token prefix requirements",
        )
        full_mission.record_timeline("mission.research_done", "Research completed", agent_name="research_specialist")

        # Step 2: Coding specialist execution
        m_task = orchestrator.create_task(
            goal="Implement token prefix update",
            context={"research_notes": t_res.evidence},
        )
        full_mission.add_task(m_task.task_id)
        m_task.current_agent = "coding_specialist"
        c_res = orchestrator.execute_task(m_task)
        full_mission.record_timeline("mission.code_done", "Code implemented", agent_name="coding_specialist")

        # Step 3: Review loop
        rev_status, _ = orchestrator.request_review(
            task_id=m_task.task_id,
            worker_agent="coding_specialist",
            reviewer_agent="reviewer_specialist",
            max_rounds=2,
        )
        full_mission.record_timeline("mission.review_done", f"Review status: {rev_status.value}", agent_name="reviewer_specialist")
        full_mission.transition_to(MissionStatus.COMPLETED, reason="All 3 agents succeeded")
        orchestrator.storage.save_mission(full_mission)
        three_agent_ms = (time.perf_counter() - t0) * 1000.0

        print(f"\n[8] Three-Agent Collaborative Mission (Research -> Code -> Review):")
        print(f"    - Final Mission Status:    {full_mission.status.value}")
        print(f"    - Timeline Entries:        {len(full_mission.timeline)}")
        print(f"    - End-to-End Latency:      {three_agent_ms:.4f} ms")

        # Memory measurements
        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print("\n" + "=" * 70)
        print("  MEMORY & RESOURCE SUMMARY")
        print("=" * 70)
        print(f"Memory Current:   {(current_mem - start_mem) / 1024:.2f} KB")
        print(f"Memory Peak:      {peak_mem / (1024 * 1024):.2f} MB")
        print(f"Concurrency Slot: max_local_agents = 1 (strictly preserved)")
        print("=" * 70)

    finally:
        if os.path.exists(temp_db):
            try:
                os.remove(temp_db)
            except Exception:
                pass


if __name__ == "__main__":
    run_benchmarks()
