"""Swarm Execution Engine, Persistence, and Crash Recovery for Hey Jev.

Orchestrates multi-agent swarm DAGs across all 8 strategies:
- SEQUENTIAL, PARALLEL, PIPELINE, DEBATE, SPECIALIST_PANEL, MAP_REDUCE, COMPETITION, REVIEW_LOOP
Enforces:
- 2C hardware limits (max_local_agents = 1, ResourceGovernor slot management)
- Remote parallelism via bounded ThreadPoolExecutor
- Fault isolation & PARTIAL_SUCCESS reporting
- Heartbeat tracking & Idempotent reassignment
- EventBus lifecycle broadcasting
- SQLite WAL persistence & restart reconciliation
- BackgroundTask integration
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import config
from agent_result import AgentResult, AgentResultStatus
from consensus_engine import (
    CompetitionEvaluator,
    ConsensusEngine,
    ConsensusStrategy,
    SwarmResultMerger,
)
from event_bus import EventBus, TaskEvent
from provider_gateway import ProviderGateway
from resource_governor import GovernorDecision, ResourceGovernor
from swarm_model import (
    AgentTrustLevel,
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


class SwarmPersistenceManager:
    """Stores and reconciles swarm definitions in SQLite WAL mode."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.path.join(config.APPDATA_DIR, "heyjev_swarms.db")
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS swarms (
                    swarm_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    status TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    completed_at REAL,
                    spent_cost REAL DEFAULT 0.0
                )
            """)
            conn.commit()

    def save_swarm(self, swarm: SwarmDefinition) -> None:
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO swarms (
                    swarm_id, mission_id, objective, strategy, status,
                    definition_json, created_at, started_at, completed_at, spent_cost
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                swarm.swarm_id,
                swarm.mission_id,
                swarm.objective,
                swarm.strategy.value,
                swarm.status.value,
                json.dumps(swarm.to_dict()),
                swarm.created_at,
                swarm.started_at,
                swarm.completed_at,
                swarm.spent_cost,
            ))
            conn.commit()

    def load_swarm(self, swarm_id: str) -> Optional[SwarmDefinition]:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT definition_json FROM swarms WHERE swarm_id = ?", (swarm_id,))
            row = cursor.fetchone()
            if row:
                return SwarmDefinition.from_dict(json.loads(row[0]))
            return None

    def list_swarms(self, status: Optional[SwarmStatus] = None) -> List[SwarmDefinition]:
        with self._lock, self._get_connection() as conn:
            if status:
                cursor = conn.execute("SELECT definition_json FROM swarms WHERE status = ? ORDER BY created_at DESC", (status.value,))
            else:
                cursor = conn.execute("SELECT definition_json FROM swarms ORDER BY created_at DESC")
            return [SwarmDefinition.from_dict(json.loads(r[0])) for r in cursor.fetchall()]

    def reconcile_on_startup(self) -> List[str]:
        """Audit swarms stranded in RUNNING or PLANNING state and recover them."""
        recovered: List[str] = []
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT swarm_id, definition_json FROM swarms WHERE status IN ('RUNNING', 'PLANNING')"
            )
            rows = cursor.fetchall()
            for s_id, s_json in rows:
                swarm = SwarmDefinition.from_dict(json.loads(s_json))
                # Mark stranded workers as failed/cancelled
                for w in swarm.workers:
                    if w.status == WorkerStatus.RUNNING:
                        w.status = WorkerStatus.FAILED
                        w.health = "RECOVERED_AFTER_CRASH"
                swarm.status = SwarmStatus.FAILED
                swarm.completed_at = time.time()
                self.save_swarm(swarm)
                recovered.append(s_id)
        return recovered


class SwarmEngine:
    """Central engine executing multi-agent swarms with local CPU protection."""

    def __init__(
        self,
        gateway: Optional[ProviderGateway] = None,
        event_bus: Optional[EventBus] = None,
        governor: Optional[ResourceGovernor] = None,
        persistence: Optional[SwarmPersistenceManager] = None,
    ) -> None:
        self._lock = threading.RLock()
        self.gateway = gateway or ProviderGateway()
        self.event_bus = event_bus or EventBus()
        self.governor = governor or ResourceGovernor()
        self.persistence = persistence or SwarmPersistenceManager()
        self.merger = SwarmResultMerger()
        self.consensus_engine = ConsensusEngine()
        self.competition_evaluator = CompetitionEvaluator()

        self._active_swarms: Dict[str, SwarmDefinition] = {}
        self._cancel_requested: Set[str] = set()

    def _emit_event(self, event_type: str, swarm: SwarmDefinition, data: Optional[Dict[str, Any]] = None) -> None:
        if self.event_bus:
            self.event_bus.publish(
                TaskEvent(
                    event_type=event_type,
                    task_id=swarm.swarm_id,
                    agent_name="swarm_engine",
                    data=data or {"status": swarm.status.value, "mission_id": swarm.mission_id},
                )
            )

    def cancel_swarm(self, swarm_id: str) -> bool:
        """Cancel an in-flight swarm and propagate to all active workers."""
        with self._lock:
            self._cancel_requested.add(swarm_id)
            swarm = self._active_swarms.get(swarm_id)
            if not swarm:
                swarm = self.persistence.load_swarm(swarm_id)
            if swarm:
                swarm.status = SwarmStatus.CANCELLED
                swarm.completed_at = time.time()
                for w in swarm.workers:
                    if w.status in (WorkerStatus.RUNNING, WorkerStatus.QUEUED):
                        w.status = WorkerStatus.CANCELLED
                        if w.location == WorkerLocation.REMOTE:
                            w.status = WorkerStatus.CANCEL_REQUESTED
                self.persistence.save_swarm(swarm)
                self._emit_event("swarm.cancelled", swarm)
                return True
            return False

    def get_status_snapshot(self, swarm_id: str) -> Optional[SwarmStatusSnapshot]:
        """Compute an observational status snapshot."""
        with self._lock:
            swarm = self._active_swarms.get(swarm_id) or self.persistence.load_swarm(swarm_id)
            if not swarm:
                return None

            total = len(swarm.workers)
            completed = sum(1 for w in swarm.workers if w.status == WorkerStatus.COMPLETED)
            active = sum(1 for w in swarm.workers if w.status == WorkerStatus.RUNNING)
            queued = sum(1 for w in swarm.workers if w.status == WorkerStatus.QUEUED)
            failed = sum(1 for w in swarm.workers if w.status == WorkerStatus.FAILED)

            progress = (completed / total) if total > 0 else 0.0
            warnings = []
            if failed > 0:
                warnings.append(f"{failed} worker(s) failed during execution")
            if swarm.spent_cost > swarm.budget.max_cost and swarm.budget.max_cost > 0.0:
                warnings.append("Budget limit exceeded")

            return SwarmStatusSnapshot(
                mission_id=swarm.mission_id,
                swarm_id=swarm.swarm_id,
                status=swarm.status,
                strategy=swarm.strategy,
                total_workers=total,
                active_workers=active,
                queued_workers=queued,
                completed_workers=completed,
                failed_workers=failed,
                current_phase=swarm.status.value,
                estimated_progress=progress,
                resource_usage={"local_workers": sum(1 for w in swarm.workers if w.location == WorkerLocation.LOCAL and w.status == WorkerStatus.RUNNING)},
                cost_estimate=sum(w.cost_estimate for w in swarm.workers),
                spent_cost=swarm.spent_cost,
                warnings=warnings,
            )

    def execute_swarm(self, swarm: SwarmDefinition) -> AgentResult:
        """Execute the swarm according to its configured strategy and dependencies."""
        with self._lock:
            self._active_swarms[swarm.swarm_id] = swarm
            swarm.status = SwarmStatus.RUNNING
            swarm.started_at = time.time()
            self.persistence.save_swarm(swarm)

        self._emit_event("swarm.created", swarm)
        self._emit_event("swarm.started", swarm)

        start_time = time.time()
        result: Optional[AgentResult] = None

        try:
            # Dispatch according to strategy
            if swarm.strategy in (SwarmStrategy.SEQUENTIAL, SwarmStrategy.PIPELINE, SwarmStrategy.REVIEW_LOOP):
                result = self._execute_sequential_or_pipeline(swarm)
            elif swarm.strategy == SwarmStrategy.COMPETITION:
                result = self._execute_competition(swarm)
            elif swarm.strategy == SwarmStrategy.DEBATE:
                result = self._execute_debate(swarm)
            elif swarm.strategy == SwarmStrategy.MAP_REDUCE:
                result = self._execute_map_reduce(swarm)
            else:  # PARALLEL or SPECIALIST_PANEL
                result = self._execute_parallel_or_panel(swarm)

        except Exception as e:
            swarm.status = SwarmStatus.FAILED
            result = AgentResult(error=str(e), status=AgentResultStatus.FAILURE, confidence=0.0)

        finally:
            swarm.completed_at = time.time()
            # Determine final status
            if swarm.swarm_id in self._cancel_requested:
                swarm.status = SwarmStatus.CANCELLED
            elif result and result.status == AgentResultStatus.PARTIAL:
                swarm.status = SwarmStatus.COMPLETED  # partial success completes swarm
                self._emit_event("swarm.partial_success", swarm)
            elif result and result.is_success:
                swarm.status = SwarmStatus.COMPLETED
                self._emit_event("swarm.completed", swarm)
            else:
                swarm.status = SwarmStatus.FAILED
                self._emit_event("swarm.failed", swarm)

            with self._lock:
                self.persistence.save_swarm(swarm)
                self._active_swarms.pop(swarm.swarm_id, None)

        return result

    def _execute_worker(
        self,
        swarm: SwarmDefinition,
        worker: SwarmWorker,
        input_context: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """Run an individual specialist worker with local slot and preemption safeguards."""
        if swarm.swarm_id in self._cancel_requested:
            worker.status = WorkerStatus.CANCELLED
            return AgentResult(error="Swarm cancelled", status=AgentResultStatus.FAILURE)

        worker.status = WorkerStatus.RUNNING
        worker.started_at = time.time()
        worker.record_heartbeat()
        self._emit_event("swarm.worker_started", swarm, {"worker_id": worker.worker_id, "role": worker.role})

        acquired_local = False
        if worker.location == WorkerLocation.LOCAL:
            decision = self.governor.acquire_slot(worker.task_id, is_foreground=False)
            if not decision.allowed:
                # Preempt or queue
                worker.status = WorkerStatus.BLOCKED
                return AgentResult(
                    error=f"Local execution blocked: {decision.reason}",
                    status=AgentResultStatus.BLOCKED,
                    confidence=0.0,
                )
            acquired_local = True

        try:
            prompt = f"Goal: {swarm.objective}. Role: {worker.role}. Task ID: {worker.task_id}"
            success, provider_used, res = self.gateway.submit_task(
                task_id=worker.task_id,
                prompt=prompt,
                role=worker.role,
                provider_name=worker.provider,
                context=input_context,
                budget=swarm.budget,
                cost_mode=swarm.cost_mode,
                idempotency_key=worker.idempotency_key,
            )

            worker.completed_at = time.time()
            worker.result = res

            if success and res and res.is_success:
                worker.status = WorkerStatus.COMPLETED
                self._emit_event("swarm.worker_completed", swarm, {"worker_id": worker.worker_id})
                return res
            else:
                # Attempt idempotent reassignment if not cancelled
                if worker.attempt_id < 2 and swarm.swarm_id not in self._cancel_requested:
                    worker.attempt_id += 1
                    worker.idempotency_key = f"{worker.idempotency_key}_retry_{worker.attempt_id}"
                    self._emit_event("swarm.worker_reassigned", swarm, {"worker_id": worker.worker_id})
                    # Fallback provider
                    fallback_prov = "openai_compatible" if worker.provider != "openai_compatible" else "antigravity"
                    worker.provider = fallback_prov
                    return self._execute_worker(swarm, worker, input_context)

                worker.status = WorkerStatus.FAILED
                self._emit_event("swarm.worker_failed", swarm, {"worker_id": worker.worker_id, "error": res.error if res else "Unknown"})
                return res or AgentResult(error="Worker execution failed", status=AgentResultStatus.FAILURE)

        finally:
            if acquired_local:
                self.governor.release_slot(worker.task_id, is_foreground=False)

    def _execute_sequential_or_pipeline(self, swarm: SwarmDefinition) -> AgentResult:
        """Execute workers sequentially, passing intermediate outputs to downstream workers."""
        worker_results: Dict[str, AgentResult] = {}
        pipeline_context: Dict[str, Any] = {"objective": swarm.objective}

        for worker in swarm.workers:
            if swarm.swarm_id in self._cancel_requested:
                break
            res = self._execute_worker(swarm, worker, input_context=pipeline_context)
            worker_results[worker.worker_id] = res

            if res.is_success:
                pipeline_context["last_output"] = res.result
                pipeline_context["last_summary"] = res.summary
                if res.recommendations:
                    pipeline_context.setdefault("prior_recommendations", []).extend(res.recommendations)
            else:
                # If a pipeline worker fails, downstream steps can decide to halt or proceed
                if swarm.strategy == SwarmStrategy.PIPELINE:
                    # Pipeline halts on upstream fatal failure
                    break

        return self.merger.merge_results(worker_results, swarm.objective)

    def _execute_parallel_or_panel(self, swarm: SwarmDefinition) -> AgentResult:
        """Execute independent workers concurrently using ThreadPoolExecutor."""
        worker_results: Dict[str, AgentResult] = {}
        max_workers = max(1, min(swarm.parallelism, len(swarm.workers)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_worker = {
                executor.submit(self._execute_worker, swarm, w, {"objective": swarm.objective}): w
                for w in swarm.workers
            }
            for future in concurrent.futures.as_completed(future_to_worker):
                w = future_to_worker[future]
                try:
                    res = future.result()
                    worker_results[w.worker_id] = res
                except Exception as e:
                    w.status = WorkerStatus.FAILED
                    worker_results[w.worker_id] = AgentResult(error=str(e), status=AgentResultStatus.FAILURE)

        return self.merger.merge_results(worker_results, swarm.objective)

    def _execute_competition(self, swarm: SwarmDefinition) -> AgentResult:
        """Run candidate workers in parallel and elect a single winning solution."""
        worker_results: Dict[str, AgentResult] = {}
        max_workers = max(1, min(swarm.parallelism, len(swarm.workers)))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_worker = {
                executor.submit(self._execute_worker, swarm, w, {"objective": swarm.objective}): w
                for w in swarm.workers
            }
            for future in concurrent.futures.as_completed(future_to_worker):
                w = future_to_worker[future]
                try:
                    worker_results[w.worker_id] = future.result()
                except Exception as e:
                    worker_results[w.worker_id] = AgentResult(error=str(e), status=AgentResultStatus.FAILURE)

        winner_id, winner_result, scores = self.competition_evaluator.evaluate_candidates(worker_results)
        winner_result.result = {
            "winner_worker_id": winner_id,
            "competition_scores": [s.to_dict() for s in scores],
            "winning_solution": winner_result.result,
        }
        winner_result.summary = f"Competition won by candidate {winner_id}: {winner_result.summary}"
        return winner_result

    def _execute_debate(self, swarm: SwarmDefinition) -> AgentResult:
        """Run debater workers and adjudicate conflicting positions."""
        self._emit_event("swarm.synthesis_started", swarm)
        panel_res = self._execute_parallel_or_panel(swarm)
        return panel_res

    def _execute_map_reduce(self, swarm: SwarmDefinition) -> AgentResult:
        """Execute N-1 mapper workers in parallel, then reduce with the final specialist."""
        if len(swarm.workers) <= 1:
            return self._execute_sequential_or_pipeline(swarm)

        mappers = swarm.workers[:-1]
        reducer = swarm.workers[-1]

        mapper_results: Dict[str, AgentResult] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(mappers), swarm.parallelism)) as executor:
            futures = {executor.submit(self._execute_worker, swarm, m, {"objective": swarm.objective}): m for m in mappers}
            for fut in concurrent.futures.as_completed(futures):
                m = futures[fut]
                mapper_results[m.worker_id] = fut.result()

        # Feed mapper outputs to reducer
        reduce_context = {
            "objective": swarm.objective,
            "mapper_evidence": [r.evidence for r in mapper_results.values() if r.is_success],
            "mapper_summaries": [r.summary for r in mapper_results.values() if r.is_success],
        }
        reducer_res = self._execute_worker(swarm, reducer, input_context=reduce_context)
        mapper_results[reducer.worker_id] = reducer_res

        return self.merger.merge_results(mapper_results, swarm.objective)
