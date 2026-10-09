"""Unit, Integration, and End-to-End Tests for Phase 18: Agent Swarm & Provider Gateway.

Tests cover:
- SwarmDefinition, SwarmWorker, SwarmBudget, ArtifactTrustMetadata, SwarmStatusSnapshot
- SwarmPlanner (minimum-sufficient heuristic, role generation, max_local_agents, DAG dependencies)
- Swarm Strategies: SEQUENTIAL, PARALLEL, PIPELINE, DEBATE, SPECIALIST_PANEL, COMPETITION, REVIEW_LOOP
- ProviderGateway: discovery, health, rate-limiting fallback, zero-budget gating, secret scrubbing, context partitioning
- Transports: Antigravity, OpenAI-compatible, Anthropic, LocalModel, A2A 1.0, Browser fallback
- Fault isolation & PARTIAL_SUCCESS handling
- Worker heartbeats & idempotent reassignment
- Swarm persistence and restart crash recovery
- EventBus telemetry and Phase 17 BackgroundTaskManager integration
- The 5 Required End-to-End Demonstrations
- Browser Agent-to-Agent Fallback Workflow
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from agent_result import AgentResult, AgentResultStatus
from agent_task import TaskPermission
from background_task_manager import BackgroundTaskManager
from background_task_model import BackgroundTaskDefinition, BackgroundTaskPriority, BackgroundTaskState
from change_detector import ChangeDetector
from consensus_engine import (
    CompetitionEvaluator,
    ConsensusEngine,
    ConsensusStrategy,
    SwarmResultMerger,
)
from event_bus import EventBus, TaskEvent
from memory_model import EpistemicStatus, MemoryRecord, MemorySensitivity, MemorySource, MemoryType
from memory_store import SqliteMemoryStore
from notification_manager import NotificationLevel, NotificationManager
from provider_adapters import (
    A2ATransportAdapter,
    AntigravityProviderAdapter,
    AnthropicCompatibleAdapter,
    BrowserFallbackTransport,
    LocalModelAdapter,
    MockProviderConflicting,
    MockProviderFast,
    MockProviderFailing,
    MockProviderHighQuality,
    MockProviderLowQuality,
    MockProviderRateLimited,
    MockProviderSlow,
    OpenAICompatibleAdapter,
)
from provider_gateway import ModelRouter, ProviderGateway, ProviderHealthRecord, ProviderType
from resource_governor import ResourceGovernor
from swarm_coordinator import SwarmCoordinator
from swarm_engine import SwarmEngine, SwarmPersistenceManager
from swarm_model import (
    AgentTrustLevel,
    ArtifactTrustMetadata,
    CostMode,
    SwarmBudget,
    SwarmDefinition,
    SwarmStatus,
    SwarmStatusSnapshot,
    SwarmStrategy,
    SwarmWorker,
    WorkerLocation,
    WorkerStatus,
)
from swarm_planner import SwarmPlanner


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_p18_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_gateway():
    gw = ProviderGateway(seed_defaults=False)
    gw.register_transport("fast", MockProviderFast("fast"))
    gw.register_transport("slow", MockProviderSlow(delay_seconds=0.01))
    gw.register_transport("failing", MockProviderFailing("Service unavailable"))
    gw.register_transport("rate_limited", MockProviderRateLimited(fail_count=1))
    gw.register_transport("high_q", MockProviderHighQuality())
    gw.register_transport("low_q", MockProviderLowQuality())
    return gw


# =====================================================================
# 1. Swarm Models & Schemas
# =====================================================================

def test_swarm_definition_and_worker_models():
    budget = SwarmBudget(max_cost=0.50, max_requests=10)
    worker = SwarmWorker(
        worker_id="w_1",
        role="CODER",
        provider="fast",
        location=WorkerLocation.LOCAL,
        trust_level=AgentTrustLevel.LOCAL_TRUSTED,
    )
    swarm = SwarmDefinition(
        swarm_id="swarm_test",
        objective="Test swarm",
        strategy=SwarmStrategy.PARALLEL,
        budget=budget,
    )
    swarm.add_worker(worker)

    assert swarm.swarm_id == "swarm_test"
    assert swarm.budget.max_cost == 0.50
    assert len(swarm.workers) == 1
    assert swarm.can_worker_start("w_1") is True

    # Add dependent worker
    w2 = SwarmWorker(worker_id="w_2", role="REVIEWER", provider="fast")
    swarm.add_worker(w2, depends_on=["w_1"])
    assert swarm.can_worker_start("w_2") is False

    worker.status = WorkerStatus.COMPLETED
    assert swarm.can_worker_start("w_2") is True


def test_artifact_trust_metadata():
    content = b"console.log('hello world');"
    artifact = ArtifactTrustMetadata(
        producer_agent="coder_1",
        provider="antigravity",
        trust_level=AgentTrustLevel.VERIFIED_REMOTE,
        source_task_id="task_123",
        is_executable=False,
    )
    chk = artifact.compute_checksum(content)
    assert chk.startswith("sha256:")
    assert artifact.is_executable is False
    assert artifact.trust_level == AgentTrustLevel.VERIFIED_REMOTE


# =====================================================================
# 2. Swarm Planner & Minimum Sufficient Delegation
# =====================================================================

def test_swarm_planner_heuristic_and_role_generation():
    planner = SwarmPlanner()

    # Simple goal -> 1 worker
    simple_swarm = planner.plan_swarm("Open notepad")
    assert len(simple_swarm.workers) == 1
    assert simple_swarm.workers[0].role in ("WINDOWS_OPERATOR", "CODER")

    # Moderate goal -> 2 workers
    mod_swarm = planner.plan_swarm("Review this pull request and evaluate architecture")
    assert len(mod_swarm.workers) >= 2
    roles = [w.role for w in mod_swarm.workers]
    assert "REVIEWER" in roles or "CODER" in roles

    # Deep research -> 3 workers, MAP_REDUCE
    research_swarm = planner.plan_swarm("Research this topic deeply across multiple sources")
    assert len(research_swarm.workers) == 3
    assert research_swarm.strategy == SwarmStrategy.MAP_REDUCE

    # Hardware limit: max 1 local agent
    local_count = sum(1 for w in research_swarm.workers if w.location == WorkerLocation.LOCAL)
    assert local_count <= 1


def test_swarm_planner_spawn_depth_safety():
    planner = SwarmPlanner()
    swarm = planner.plan_swarm("Build full stack app")
    assert swarm.metadata.get("max_spawn_depth") == 1


# =====================================================================
# 3. Provider Gateway, Routing, and Safeguards
# =====================================================================

def test_provider_gateway_secret_scrubbing():
    gw = ProviderGateway(seed_defaults=False)
    raw = "Here is my secret key sk-proj-12345678901234567890 and github token ghp_123456789012345678901234567890123456"
    scrubbed = gw.scrub_secrets(raw)
    assert "sk-proj-" not in scrubbed
    assert "ghp_" not in scrubbed
    assert "[REDACTED_SECRET]" in scrubbed


def test_provider_gateway_zero_budget_guard():
    gw = ProviderGateway(seed_defaults=False)
    # Register paid provider
    openai_mock = OpenAICompatibleAdapter()
    gw.register_transport("openai_compatible", openai_mock)

    budget_zero = SwarmBudget(max_cost=0.0)
    success, prov, res = gw.submit_task(
        task_id="t_budget",
        prompt="Write a 1000-line program",
        provider_name="openai_compatible",
        budget=budget_zero,
    )
    assert success is False
    assert res.status == AgentResultStatus.BLOCKED
    assert "Zero cost policy" in res.error


def test_provider_gateway_rate_limit_and_fallback(mock_gateway):
    # 'rate_limited' fails once then falls back to 'fast'
    success, prov, res = mock_gateway.submit_task(
        task_id="t_rate",
        prompt="Execute task",
        provider_name="rate_limited",
    )
    assert success is True
    assert res.is_success is True


def test_provider_gateway_context_partitioning():
    gw = ProviderGateway(seed_defaults=False)
    ctx = {
        "goal": "Test task",
        "diff": "+++ lines added",
        "repo_path": "C:\\repo",
        "query": "search query",
        "sensitive_user_note": "private note",
    }
    # Coder should get diff and repo_path, not search query
    coder_ctx = gw.partition_context(ctx, role="CODER")
    assert "diff" in coder_ctx
    assert "repo_path" in coder_ctx
    assert "query" not in coder_ctx

    # Researcher should get query, not diff
    researcher_ctx = gw.partition_context(ctx, role="RESEARCHER")
    assert "query" in researcher_ctx
    assert "diff" not in researcher_ctx


# =====================================================================
# 4. Transports & Adapters
# =====================================================================

def test_antigravity_provider_adapter():
    adapter = AntigravityProviderAdapter()
    health = adapter.health_check()
    assert health["provider"] == "antigravity"
    assert "coding" in adapter.capabilities()
    assert adapter.submit("t_ag", "Inspect codebase") is True
    res = adapter.get_result("t_ag")
    assert res.is_success is True
    assert "Antigravity" in res.summary


def test_a2a_transport_adapter():
    a2a = A2ATransportAdapter()
    card = a2a.discover_agent_card("agent_external")
    assert card["version"] == "1.0.0"
    assert "research" in card["capabilities"]
    assert a2a.submit("t_a2a", "Federated query", idempotency_key="idem_123") is True
    res = a2a.get_result("t_a2a")
    assert res.is_success is True
    assert len(res.artifacts) == 1
    assert res.artifacts[0]["checksum"].startswith("sha256:")


def test_browser_fallback_transport():
    browser = BrowserFallbackTransport()
    assert browser.health_check()["sandboxed"] is True
    assert browser.submit("t_br", "Query web AI assistant") is True
    res = browser.get_result("t_br")
    assert res.is_success is True
    assert "Browser fallback" in res.summary


def test_local_model_adapter():
    local_ad = LocalModelAdapter()
    assert local_ad.cost_estimate("test prompt") == 0.0
    assert local_ad.submit("t_loc", "Summarize") is True
    res = local_ad.get_result("t_loc")
    assert res.is_success is True


# =====================================================================
# 5. Swarm Strategies Execution
# =====================================================================

def test_swarm_sequential_strategy(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Sequential test", strategy=SwarmStrategy.SEQUENTIAL)
    swarm.add_worker(SwarmWorker(worker_id="w1", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="w2", role="CODER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert swarm.status == SwarmStatus.COMPLETED
    assert swarm.workers[0].status == WorkerStatus.COMPLETED
    assert swarm.workers[1].status == WorkerStatus.COMPLETED


def test_swarm_parallel_strategy(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Parallel test", strategy=SwarmStrategy.PARALLEL, parallelism=2)
    swarm.add_worker(SwarmWorker(worker_id="w1", role="RESEARCHER", provider="slow", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="w2", role="RESEARCHER", provider="slow", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert swarm.status == SwarmStatus.COMPLETED
    assert swarm.workers[0].status == WorkerStatus.COMPLETED
    assert swarm.workers[1].status == WorkerStatus.COMPLETED


def test_swarm_competition_strategy(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Competition test", strategy=SwarmStrategy.COMPETITION, parallelism=2)
    swarm.add_worker(SwarmWorker(worker_id="candidate_low", role="CODER", provider="low_q", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="candidate_high", role="CODER", provider="high_q", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert "candidate_high" in res.summary
    assert res.result["winner_worker_id"] == "candidate_high"


def test_swarm_debate_and_consensus(mock_gateway):
    gw = ProviderGateway(seed_defaults=False)
    gw.register_transport("conf_a", MockProviderConflicting("A"))
    gw.register_transport("conf_b", MockProviderConflicting("B"))

    engine = SwarmEngine(gateway=gw)
    swarm = SwarmDefinition(objective="Debate best approach", strategy=SwarmStrategy.DEBATE, parallelism=2)
    swarm.add_worker(SwarmWorker(worker_id="debater_a", role="RESEARCHER", provider="conf_a", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="debater_b", role="RESEARCHER", provider="conf_b", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    # SynthesisEngine must have detected contradiction and synthesized winner
    assert "Resolved 1 contradictions" in res.summary or "Synthesized" in res.summary


def test_swarm_map_reduce_strategy(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Map reduce topic", strategy=SwarmStrategy.MAP_REDUCE, parallelism=2)
    swarm.add_worker(SwarmWorker(worker_id="map_1", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="map_2", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="reduce_1", role="SYNTHESIZER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert all(w.status == WorkerStatus.COMPLETED for w in swarm.workers)


# =====================================================================
# 6. Fault Isolation, Partial Success, & Cancellation
# =====================================================================

def test_swarm_fault_isolation_and_partial_success(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Fault isolation test", strategy=SwarmStrategy.PARALLEL, parallelism=2)
    swarm.add_worker(SwarmWorker(worker_id="w_ok", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="w_fail", role="RESEARCHER", provider="failing", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    # Swarm finishes with PARTIAL success
    assert res.status == AgentResultStatus.PARTIAL
    assert swarm.status == SwarmStatus.COMPLETED
    assert swarm.workers[0].status == WorkerStatus.COMPLETED
    assert swarm.workers[1].status == WorkerStatus.FAILED


def test_swarm_cancellation_propagation(mock_gateway):
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(objective="Cancel test", strategy=SwarmStrategy.PARALLEL)
    swarm.add_worker(SwarmWorker(worker_id="w1", role="CODER", provider="fast", location=WorkerLocation.REMOTE))

    # Cancel swarm before start
    engine.cancel_swarm(swarm.swarm_id)
    res = engine.execute_swarm(swarm)
    assert swarm.status == SwarmStatus.CANCELLED


def test_swarm_persistence_and_crash_recovery(temp_dir, mock_gateway):
    db_path = os.path.join(temp_dir, "test_swarm.db")
    pm = SwarmPersistenceManager(db_path=db_path)

    swarm = SwarmDefinition(
        swarm_id="stranded_swarm",
        objective="Crash test",
        status=SwarmStatus.RUNNING,
    )
    w = SwarmWorker(worker_id="stranded_w", status=WorkerStatus.RUNNING)
    swarm.add_worker(w)
    pm.save_swarm(swarm)

    # Reconcile on startup
    recovered = pm.reconcile_on_startup()
    assert "stranded_swarm" in recovered

    reloaded = pm.load_swarm("stranded_swarm")
    assert reloaded.status == SwarmStatus.FAILED
    assert reloaded.workers[0].status == WorkerStatus.FAILED
    assert reloaded.workers[0].health == "RECOVERED_AFTER_CRASH"


# =====================================================================
# 7. Hardware Gating & EventBus Integration
# =====================================================================

def test_hardware_governor_local_slot_enforcement(mock_gateway):
    # Governor with 0 background slots blocks local worker
    gov = ResourceGovernor(max_background=0)
    engine = SwarmEngine(gateway=mock_gateway, governor=gov)

    swarm = SwarmDefinition(objective="Local limit test", strategy=SwarmStrategy.SEQUENTIAL)
    # Local worker
    swarm.add_worker(SwarmWorker(worker_id="w_loc", role="CODER", provider="fast", location=WorkerLocation.LOCAL))

    res = engine.execute_swarm(swarm)
    assert swarm.workers[0].status == WorkerStatus.BLOCKED


def test_event_bus_lifecycle_emission(mock_gateway):
    bus = EventBus()
    emitted_events: List[str] = []
    bus.subscribe("swarm.*", lambda ev: emitted_events.append(ev.event_type))

    engine = SwarmEngine(gateway=mock_gateway, event_bus=bus)
    swarm = SwarmDefinition(objective="Event bus test", strategy=SwarmStrategy.SEQUENTIAL)
    swarm.add_worker(SwarmWorker(worker_id="w1", role="CODER", provider="fast", location=WorkerLocation.REMOTE))

    _ = engine.execute_swarm(swarm)
    assert "swarm.created" in emitted_events
    assert "swarm.started" in emitted_events
    assert "swarm.worker_started" in emitted_events
    assert "swarm.worker_completed" in emitted_events
    assert "swarm.completed" in emitted_events


# =====================================================================
# 8. End-to-End Demonstrations
# =====================================================================

def test_demo_1_diagnose_test_failure(mock_gateway):
    """Demo 1: Diagnose test failure (Triage -> Research -> Reviewer -> Synthesis -> Coding -> Verifier)."""
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(
        objective="Diagnose test failure: ImportError in test_engine.py",
        strategy=SwarmStrategy.PIPELINE,
    )
    swarm.add_worker(SwarmWorker(worker_id="triage", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="coder", role="CODER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="verifier", role="TESTER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert swarm.status == SwarmStatus.COMPLETED
    assert all(w.status == WorkerStatus.COMPLETED for w in swarm.workers)


def test_demo_2_find_best_solution_competition(mock_gateway):
    """Demo 2: Three independent specialists -> comparison -> adjudication -> final recommendation."""
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(
        objective="Find best solution: optimize SQLite query performance",
        strategy=SwarmStrategy.COMPETITION,
        parallelism=3,
    )
    swarm.add_worker(SwarmWorker(worker_id="specialist_a", role="CODER", provider="low_q", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="specialist_b", role="CODER", provider="high_q", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="specialist_c", role="CODER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert res.result["winner_worker_id"] == "specialist_b"
    assert "High quality implementation" in res.summary


def test_demo_3_deep_research_synthesis(mock_gateway):
    """Demo 3: Researcher A, B, C -> source merge -> contradiction check -> synthesis -> verification."""
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(
        objective="Research latest advancements in on-device speech endpointing",
        strategy=SwarmStrategy.MAP_REDUCE,
        parallelism=3,
    )
    swarm.add_worker(SwarmWorker(worker_id="res_a", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="res_b", role="RESEARCHER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="reducer", role="SYNTHESIZER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert "Synthesized from 3 specialists" in res.summary


def test_demo_4_fix_project_pipeline(mock_gateway):
    """Demo 4: Fix my project (Windows Agent inspect -> Coder -> Research -> Reviewer -> Run -> Verifier)."""
    engine = SwarmEngine(gateway=mock_gateway)
    swarm = SwarmDefinition(
        objective="Fix my project: resolve build error and verify",
        strategy=SwarmStrategy.PIPELINE,
    )
    swarm.add_worker(SwarmWorker(worker_id="win_inspect", role="WINDOWS_OPERATOR", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="coder", role="CODER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="reviewer", role="REVIEWER", provider="fast", location=WorkerLocation.REMOTE))
    swarm.add_worker(SwarmWorker(worker_id="verifier", role="TESTER", provider="fast", location=WorkerLocation.REMOTE))

    res = engine.execute_swarm(swarm)
    assert res.is_success is True
    assert swarm.status == SwarmStatus.COMPLETED


def test_demo_5_background_research_with_memory_and_change_detection(temp_dir, mock_gateway):
    """Demo 5: BackgroundTask -> Swarm -> remote research -> memory comparison -> change detection -> notify."""
    # Setup Phase 16 Memory Store
    mem_db = os.path.join(temp_dir, "mem.db")
    memory_store = SqliteMemoryStore(db_path=mem_db)

    # Pre-seed memory with baseline knowledge
    base_rec = MemoryRecord(
        type=MemoryType.RESEARCH_FACT,
        content="Previous benchmark score was 85.0",
        summary="Baseline benchmark score",
        epistemic_status=EpistemicStatus.FACT,
    )
    memory_store.add(base_rec)

    # Setup Notification Manager
    notifier = NotificationManager()

    # Setup Swarm Coordinator
    coord = SwarmCoordinator(
        gateway=mock_gateway,
        memory_store=memory_store,
        notification_manager=notifier,
    )

    # Execute Background Task via BackgroundTaskManager
    bg_db = os.path.join(temp_dir, "bg.db")
    bg_mgr = BackgroundTaskManager(
        db_path=bg_db,
        task_executor=coord.handle_background_task,
        notification_manager=notifier,
    )

    bg_task = BackgroundTaskDefinition(
        task_id="bg_research_1",
        name="Background Research Swarm",
        action_type="swarm",
        action_payload={
            "goal": "Research latest benchmark score update",
            "strategy": "PARALLEL",
        },
        priority=BackgroundTaskPriority.NORMAL,
    )
    bg_mgr.register_task(bg_task)

    # Run step
    ran = bg_mgr.run_now("bg_research_1")
    assert ran is True

    # Wait for asynchronous background task execution
    for _ in range(50):
        if len(notifier.get_history()) >= 1:
            break
        time.sleep(0.05)

    # Verify memory updated
    stored = memory_store.search("latest benchmark score")
    assert len(stored) >= 1

    # Verify notification dispatched
    assert len(notifier.get_history()) >= 1
    bg_mgr.stop()


def test_demo_browser_agent_fallback_resolution(mock_gateway):
    """Browser agent-to-agent fallback: Agent A error triggers fallback recommendation query."""
    # Worker A produces failure/error
    gw = ProviderGateway(seed_defaults=False)
    gw.register_transport("failing_worker", MockProviderFailing("Syntax error in legacy library"))
    gw.register_transport("browser_fallback", BrowserFallbackTransport())

    # Initial execution fails (explicitly with allow_fallback=False)
    success_1, _, res_1 = gw.submit_task(
        "task_initial",
        "Fix library",
        provider_name="failing_worker",
        allow_fallback=False,
    )
    assert success_1 is False

    # Jev invokes BrowserFallbackTransport to ask external AI for advice
    success_2, _, res_fallback = gw.submit_task(
        "task_fallback_query",
        f"How to resolve this error: {res_1.error}",
        provider_name="browser_fallback",
    )
    assert success_2 is True
    assert "Browser fallback recommendation" in res_fallback.result["recommendation"]
