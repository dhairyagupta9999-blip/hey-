"""Phase 19 Reliability, Verification, Recovery, and Undo Benchmark for Hey Jev.

Measures:
- Action Logging latency (Write-Ahead intent logging to SQLite WAL + hash chain)
- Checkpoint persistence & loading latency with SHA-256 integrity verification
- State Reconciliation latency (Filesystem, Process, Git)
- VerificationEngineV2 multi-tier evidence collection latency (Levels 0 - 3)
- FailureClassifierV2 classification and RecoveryDecisionEngine planning latency
- CircuitBreaker trip, block, and probe recovery latency
- Saga Transaction begin, commit, and compensation latency
- UndoManager preview and execution latency
- Timeline cryptographic hash-chain verification latency
- Tracemalloc memory delta & peak RAM usage

Emits:
- benchmark_reliability_results.json
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import tracemalloc
from typing import Any, Dict, List

from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask
from reconciliation_engine import ReconciliationEngine
from recovery_engine import (
    CircuitBreakerManager,
    FailureClassifierV2,
    RecoveryDecisionEngine,
)
from reliability_manager import ReliabilityManager
from reliability_model import (
    ActionRecord,
    Checkpoint,
    CircuitBreaker,
    CircuitBreakerState,
    ExecutionStatus,
    FailureCategory,
    JevTransaction,
    JevTransactionState,
    ReversibilityType,
    VerificationLevel,
)
from undo_manager import OperationTimeline, SemanticLockManager, UndoManager
from verification_engine_v2 import VerificationEngineV2


def percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] + (s[c] - s[f]) * d


def run_benchmarks() -> Dict[str, Any]:
    print("======================================================================")
    print(" HEY JEV - PHASE 19 RELIABILITY, RECOVERY & UNDO BENCHMARK")
    print("======================================================================")
    tracemalloc.start()
    results: Dict[str, Any] = {"phase": "19", "timestamp": time.time()}

    temp_dir = tempfile.mkdtemp(prefix="heyjev_phase19_bench_")
    db_path = os.path.join(temp_dir, "benchmark_reliability.db")

    try:
        rel_mgr = ReliabilityManager(db_path=db_path)
        ver_engine = VerificationEngineV2()
        rec_engine = ReconciliationEngine()
        classifier = FailureClassifierV2()
        decision_engine = RecoveryDecisionEngine()

        # -----------------------------------------------------------------
        # 1. Action Logging (Write-Ahead SQLite WAL + Hash Chaining)
        # -----------------------------------------------------------------
        print("[1/9] Benchmarking Action Logging (WAL + Hash Chain)...")
        action_latencies: List[float] = []
        for i in range(100):
            act = ActionRecord(
                task_id=f"bench_task_{i}",
                action_type="file_write",
                target=f"C:/mock/path_{i}.txt",
                status=ExecutionStatus.ATTEMPTED,
                reversibility=ReversibilityType.REVERSIBLE,
                compensation_action={"action": "delete_file", "path": f"C:/mock/path_{i}.txt"},
            )
            t0 = time.perf_counter()
            rel_mgr.record_intent(act)
            rel_mgr.record_outcome(act, ExecutionStatus.SUCCEEDED)
            action_latencies.append((time.perf_counter() - t0) * 1000.0)

        results["action_logging_ms"] = {
            "p50": round(percentile(action_latencies, 50), 4),
            "p95": round(percentile(action_latencies, 95), 4),
            "mean": round(sum(action_latencies) / len(action_latencies), 4),
            "total_records": len(action_latencies),
        }

        # -----------------------------------------------------------------
        # 2. Checkpoint Save & Load (SHA-256 Checksumming)
        # -----------------------------------------------------------------
        print("[2/9] Benchmarking Checkpoint Save & Verification...")
        chk_save_latencies: List[float] = []
        chk_load_latencies: List[float] = []
        for i in range(50):
            chk = Checkpoint(
                task_id="bench_task_chk",
                sequence=i,
                state="RUNNING",
                completed_steps=[f"step_{j}" for j in range(i)],
                pending_steps=[f"step_{j}" for j in range(i, i + 5)],
                environment_snapshot={"cpu_percent": 12.5, "active_pid": 1000 + i},
            )
            t0 = time.perf_counter()
            rel_mgr.save_checkpoint(chk)
            chk_save_latencies.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            loaded = rel_mgr.load_latest_checkpoint("bench_task_chk")
            chk_load_latencies.append((time.perf_counter() - t1) * 1000.0)
            assert loaded is not None
            assert loaded.sequence == i

        results["checkpoint_save_ms"] = {
            "p50": round(percentile(chk_save_latencies, 50), 4),
            "p95": round(percentile(chk_save_latencies, 95), 4),
            "mean": round(sum(chk_save_latencies) / len(chk_save_latencies), 4),
        }
        results["checkpoint_load_ms"] = {
            "p50": round(percentile(chk_load_latencies, 50), 4),
            "p95": round(percentile(chk_load_latencies, 95), 4),
            "mean": round(sum(chk_load_latencies) / len(chk_load_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 3. State Reconciliation Latency
        # -----------------------------------------------------------------
        print("[3/9] Benchmarking State Reconciliation Engine...")
        sample_file_1 = os.path.join(temp_dir, "recon_file_1.txt")
        sample_file_2 = os.path.join(temp_dir, "recon_file_2.txt")
        with open(sample_file_1, "w", encoding="utf-8") as f:
            f.write("test content for reconciliation")

        recon_latencies: List[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            _ = rec_engine.reconcile_filesystem(
                expected_files_created=[sample_file_1],
                expected_files_deleted=[sample_file_2],
            )
            recon_latencies.append((time.perf_counter() - t0) * 1000.0)

        results["reconciliation_ms"] = {
            "p50": round(percentile(recon_latencies, 50), 4),
            "p95": round(percentile(recon_latencies, 95), 4),
            "mean": round(sum(recon_latencies) / len(recon_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 4. Multi-Level Verification Engine (V2)
        # -----------------------------------------------------------------
        print("[4/9] Benchmarking VerificationEngineV2 Evidence Collector...")
        import hashlib
        with open(sample_file_1, "rb") as f:
            file_chk = f"sha256:{hashlib.sha256(f.read()).hexdigest()}"

        mock_task = AgentTask(description="Write config file", owner_agent="coder")
        mock_res = AgentResult(
            status=AgentResultStatus.SUCCESS,
            result={"file": sample_file_1},
            summary="File created",
        )

        ver_latencies: List[float] = []
        for _ in range(50):
            t0 = time.perf_counter()
            _ = ver_engine.verify_with_evidence(
                task=mock_task,
                result=mock_res,
                target_path=sample_file_1,
                expected_checksum=file_chk,
            )
            ver_latencies.append((time.perf_counter() - t0) * 1000.0)

        results["verification_evidence_ms"] = {
            "p50": round(percentile(ver_latencies, 50), 4),
            "p95": round(percentile(ver_latencies, 95), 4),
            "mean": round(sum(ver_latencies) / len(ver_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 5. Failure Classification & Recovery Decision Engine
        # -----------------------------------------------------------------
        print("[5/9] Benchmarking Failure Classification & Decision Engine...")
        sample_errors = [
            "HTTP 429 Too Many Requests: Rate limit exceeded for model",
            "Connection reset by peer: network socket closed unexpectedly",
            "FileNotFoundError: expected output artifact not found on disk",
            "WinError 5: Access is denied while modifying registry key",
            "Checksum mismatch: expected sha256:abc, got sha256:xyz",
        ]
        class_latencies: List[float] = []
        plan_latencies: List[float] = []

        for err in sample_errors * 10:
            t0 = time.perf_counter()
            cat = classifier.classify(err)
            class_latencies.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            _ = decision_engine.plan_recovery(cat, target_id="worker_target_1", attempt_count=1)
            plan_latencies.append((time.perf_counter() - t1) * 1000.0)

        results["classification_ms"] = {
            "p50": round(percentile(class_latencies, 50), 4),
            "p95": round(percentile(class_latencies, 95), 4),
            "mean": round(sum(class_latencies) / len(class_latencies), 4),
        }
        results["recovery_planning_ms"] = {
            "p50": round(percentile(plan_latencies, 50), 4),
            "p95": round(percentile(plan_latencies, 95), 4),
            "mean": round(sum(plan_latencies) / len(plan_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 6. Circuit Breaker Protection
        # -----------------------------------------------------------------
        print("[6/9] Benchmarking Circuit Breaker Tripping & State Transition...")
        cb_mgr = CircuitBreakerManager()
        cb = cb_mgr.get_or_create("bench_service", failure_threshold=3, cooldown_seconds=0.05)
        cb_latencies: List[float] = []

        for _ in range(50):
            t0 = time.perf_counter()
            _ = cb.can_execute()
            cb_latencies.append((time.perf_counter() - t0) * 1000.0)

        # Trip breaker
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN
        assert not cb.can_execute()

        results["circuit_breaker_check_ms"] = {
            "p50": round(percentile(cb_latencies, 50), 4),
            "p95": round(percentile(cb_latencies, 95), 4),
            "mean": round(sum(cb_latencies) / len(cb_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 7. Saga Transactions Lifecycle
        # -----------------------------------------------------------------
        print("[7/9] Benchmarking Saga Transactions (Commit & Compensate)...")
        tx_commit_latencies: List[float] = []
        tx_comp_latencies: List[float] = []

        for i in range(30):
            tx = rel_mgr.begin_transaction(mission_id=f"bench_m_{i}")
            t0 = time.perf_counter()
            rel_mgr.commit_transaction(tx)
            tx_commit_latencies.append((time.perf_counter() - t0) * 1000.0)

            # Compensating transaction benchmark
            tx_fail = rel_mgr.begin_transaction(mission_id=f"bench_m_fail_{i}")
            tx_fail.compensation_actions.append({"action": "custom_noop"})
            t1 = time.perf_counter()
            rel_mgr.compensate_transaction(tx_fail)
            tx_comp_latencies.append((time.perf_counter() - t1) * 1000.0)

        results["transaction_commit_ms"] = {
            "p50": round(percentile(tx_commit_latencies, 50), 4),
            "p95": round(percentile(tx_commit_latencies, 95), 4),
            "mean": round(sum(tx_commit_latencies) / len(tx_commit_latencies), 4),
        }
        results["transaction_compensate_ms"] = {
            "p50": round(percentile(tx_comp_latencies, 50), 4),
            "p95": round(percentile(tx_comp_latencies, 95), 4),
            "mean": round(sum(tx_comp_latencies) / len(tx_comp_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 8. Undo Manager Preview & Execution
        # -----------------------------------------------------------------
        print("[8/9] Benchmarking Undo Manager Reversal...")
        undo_mgr = rel_mgr.undo_manager
        undo_preview_latencies: List[float] = []
        undo_exec_latencies: List[float] = []

        for i in range(20):
            test_target = os.path.join(temp_dir, f"undo_target_{i}.txt")
            with open(test_target, "w", encoding="utf-8") as f:
                f.write("undo benchmark sample")

            act = ActionRecord(
                task_id=f"undo_bench_{i}",
                action_type="create_file",
                target=test_target,
                status=ExecutionStatus.SUCCEEDED,
                reversibility=ReversibilityType.REVERSIBLE,
                compensation_action={"action": "delete_file", "path": test_target},
            )
            rel_mgr.timeline.record_action(act)

            t0 = time.perf_counter()
            _ = undo_mgr.preview_undo(act.action_id)
            undo_preview_latencies.append((time.perf_counter() - t0) * 1000.0)

            t1 = time.perf_counter()
            success = undo_mgr.undo(act.action_id)
            undo_exec_latencies.append((time.perf_counter() - t1) * 1000.0)
            assert success is True
            assert not os.path.exists(test_target)

        results["undo_preview_ms"] = {
            "p50": round(percentile(undo_preview_latencies, 50), 4),
            "p95": round(percentile(undo_preview_latencies, 95), 4),
            "mean": round(sum(undo_preview_latencies) / len(undo_preview_latencies), 4),
        }
        results["undo_execution_ms"] = {
            "p50": round(percentile(undo_exec_latencies, 50), 4),
            "p95": round(percentile(undo_exec_latencies, 95), 4),
            "mean": round(sum(undo_exec_latencies) / len(undo_exec_latencies), 4),
        }

        # -----------------------------------------------------------------
        # 9. Cryptographic Hash Chain Verification & Memory Profiling
        # -----------------------------------------------------------------
        print("[9/9] Benchmarking Cryptographic Timeline Hash Chain Integrity...")
        t0 = time.perf_counter()
        integrity_ok, errors = rel_mgr.timeline.verify_integrity()
        hash_chain_latency = (time.perf_counter() - t0) * 1000.0
        if not integrity_ok:
            print(f"  [DEBUG] Integrity errors ({len(errors)}):")
            for e in errors[:10]:
                print(f"    - {e}")
        assert integrity_ok is True, f"Hash chain integrity failed: {errors[:3]}"
        assert len(errors) == 0

        results["hash_chain_verification_ms"] = round(hash_chain_latency, 4)
        results["timeline_record_count"] = len(rel_mgr.timeline.list_records(1000))

        current_ram, peak_ram = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        results["memory_profile"] = {
            "current_ram_kb": round(current_ram / 1024.0, 2),
            "peak_ram_kb": round(peak_ram / 1024.0, 2),
            "peak_ram_mb": round(peak_ram / (1024.0 * 1024.0), 3),
        }

        print("======================================================================")
        print(" BENCHMARK RESULTS SUMMARY:")
        print(f" - Action WAL Logging Latency (p50): {results['action_logging_ms']['p50']} ms")
        print(f" - Checkpoint Save Latency (p50):   {results['checkpoint_save_ms']['p50']} ms")
        print(f" - Checkpoint Load Latency (p50):   {results['checkpoint_load_ms']['p50']} ms")
        print(f" - Verification Evidence (p50):     {results['verification_evidence_ms']['p50']} ms")
        print(f" - Recovery Decision Plan (p50):    {results['recovery_planning_ms']['p50']} ms")
        print(f" - Saga Tx Commit Latency (p50):    {results['transaction_commit_ms']['p50']} ms")
        print(f" - Physical Undo Execution (p50):   {results['undo_execution_ms']['p50']} ms")
        print(f" - Hash Chain Verify ({results['timeline_record_count']} recs):   {results['hash_chain_verification_ms']} ms")
        print(f" - Peak RAM Overhead:               {results['memory_profile']['peak_ram_mb']} MB")
        print("======================================================================")

        out_path = os.path.join(os.path.dirname(__file__), "benchmark_reliability_results.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        print(f"Saved benchmark results to {out_path}")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return results


if __name__ == "__main__":
    run_benchmarks()
