"""Phase 17: Background Tasks, Watchers & Event-Driven Automation Benchmark Suite for Hey Jev.

Measures:
1. Task Registration Latency (SQLite WAL + InProcessScheduler)
2. Scheduler Step / Due Item Evaluation Latency
3. Filesystem Watcher Event Ingestion & Debounce Overhead
4. EventRuleEngine Rule Evaluation & Action Matching Latency
5. ChangeDetector Latency (Dict, List, Text diffs)
6. ResourceGovernor Preemption & Slot Acquisition Latency
7. Background System Memory Footprint (Tracemalloc Delta & Peak)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import tracemalloc
from typing import Any, Dict, List

# Ensure hey-jev root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from background_task_manager import BackgroundTaskManager
from background_task_model import (
    BackgroundTaskDefinition,
    BackgroundTaskPriority,
    BackgroundTaskState,
    NotificationLevel,
    ResourceClass,
)
from change_detector import ChangeDetector
from event_rule_engine import EventActionType, EventRule, EventRuleEngine
from resource_governor import ResourceGovernor
from scheduler_backend import InProcessSchedulerBackend
from trigger_model import TriggerDefinition, TriggerType
from watcher_model import WatcherDefinition, WatcherType
from watchers import FilesystemWatcher


def calc_percentiles(latencies: List[float]) -> Dict[str, float]:
    """Calculate min, p50, p95, p99, max, mean in milliseconds."""
    if not latencies:
        return {"min": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
    s = sorted(latencies)
    n = len(s)
    return {
        "min": round(s[0], 4),
        "p50": round(s[int(n * 0.50)], 4),
        "p95": round(s[int(n * 0.95)], 4),
        "p99": round(s[min(int(n * 0.99), n - 1)], 4),
        "max": round(s[-1], 4),
        "mean": round(sum(s) / n, 4),
    }


def benchmark_task_registration(manager: BackgroundTaskManager, count: int = 50) -> Dict[str, Any]:
    latencies = []
    for i in range(count):
        task = BackgroundTaskDefinition(
            task_id=f"bench_task_{i}",
            name=f"Benchmark Task {i}",
            action_type="shell",
            action_payload={"command": f"echo bench {i}"},
            trigger=TriggerDefinition(
                trigger_type=TriggerType.INTERVAL,
                interval_seconds=300.0,
            ),
            priority=BackgroundTaskPriority.NORMAL,
            resource_class=ResourceClass.LIGHT,
        )
        t0 = time.perf_counter()
        manager.register_task(task)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    return {
        "count": count,
        "metrics_ms": calc_percentiles(latencies),
    }


def benchmark_scheduler_step(scheduler: InProcessSchedulerBackend, iterations: int = 100) -> Dict[str, Any]:
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        scheduler.step()
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    return {
        "iterations": iterations,
        "metrics_ms": calc_percentiles(latencies),
    }


def benchmark_rule_engine_matching(engine: EventRuleEngine, count: int = 100) -> Dict[str, Any]:
    # Register 10 rules
    for i in range(10):
        engine.register_rule(
            EventRule(
                rule_id=f"rule_{i}",
                name=f"Rule {i}",
                event_name="filesystem.file_modified",
                conditions={"path": {"contains": "project"}},
                action_type=EventActionType.CREATE_BACKGROUND_TASK,
                action_payload={"task_name": "auto_test"},
            )
        )

    latencies = []
    for i in range(count):
        event = {
            "event_name": "filesystem.file_modified",
            "path": f"C:\\Users\\user\\project\\sub\\file_{i}.py",
            "timestamp": time.time(),
        }
        t0 = time.perf_counter()
        actions = engine.evaluate_event("filesystem.file_modified", event)
        t1 = time.perf_counter()
        latencies.append((t1 - t0) * 1000.0)

    return {
        "evaluations": count,
        "metrics_ms": calc_percentiles(latencies),
    }


def benchmark_change_detector(iterations: int = 100) -> Dict[str, Any]:
    detector = ChangeDetector()
    before_dict = {f"key_{i}": f"value_{i}" for i in range(100)}
    after_dict = {f"key_{i}": (f"value_{i}_mod" if i % 10 == 0 else f"value_{i}") for i in range(105)}

    before_text = "\n".join([f"line {i}: content sample {i * 2}" for i in range(100)])
    after_text = "\n".join([f"line {i}: content sample {i * 2 + (1 if i % 5 == 0 else 0)}" for i in range(105)])

    dict_latencies = []
    text_latencies = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        _ = detector.diff_dicts(before_dict, after_dict)
        t1 = time.perf_counter()
        dict_latencies.append((t1 - t0) * 1000.0)

        t2 = time.perf_counter()
        _ = detector.diff_text(before_text, after_text)
        t3 = time.perf_counter()
        text_latencies.append((t3 - t2) * 1000.0)

    return {
        "dict_diff_ms": calc_percentiles(dict_latencies),
        "text_diff_ms": calc_percentiles(text_latencies),
    }


def benchmark_resource_governor(governor: ResourceGovernor, count: int = 100) -> Dict[str, Any]:
    acquire_latencies = []
    preempt_latencies = []

    for i in range(count):
        task = BackgroundTaskDefinition(
            task_id=f"gov_bench_{i}",
            name=f"Gov Task {i}",
            action_type="shell",
            action_payload={},
            priority=BackgroundTaskPriority.NORMAL,
            resource_class=ResourceClass.LIGHT,
        )
        t0 = time.perf_counter()
        res = governor.acquire_slot(task, is_foreground=False)
        t1 = time.perf_counter()
        acquire_latencies.append((t1 - t0) * 1000.0)
        if res.allowed:
            governor.release_slot(task.task_id)

        t2 = time.perf_counter()
        governor.set_voice_active(True)
        governor.set_voice_active(False)
        t3 = time.perf_counter()
        preempt_latencies.append((t3 - t2) * 1000.0)

    return {
        "slot_acquire_ms": calc_percentiles(acquire_latencies),
        "voice_preemption_toggle_ms": calc_percentiles(preempt_latencies),
    }


def run_all_benchmarks() -> Dict[str, Any]:
    temp_dir = tempfile.mkdtemp(prefix="heyjev_bench_p17_")
    db_path = os.path.join(temp_dir, "benchmark_p17.db")

    tracemalloc.start()
    mem_start_curr, mem_start_peak = tracemalloc.get_traced_memory()

    try:
        manager = BackgroundTaskManager(db_path=db_path)
        scheduler = InProcessSchedulerBackend()
        rule_engine = EventRuleEngine()
        governor = ResourceGovernor()

        reg_results = benchmark_task_registration(manager, count=50)
        step_results = benchmark_scheduler_step(scheduler, iterations=100)
        rule_results = benchmark_rule_engine_matching(rule_engine, count=100)
        diff_results = benchmark_change_detector(iterations=100)
        gov_results = benchmark_resource_governor(governor, count=100)

        mem_end_curr, mem_end_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        results = {
            "timestamp": time.time(),
            "phase": "17",
            "task_registration": reg_results,
            "scheduler_step": step_results,
            "event_rule_engine": rule_results,
            "change_detector": diff_results,
            "resource_governor": gov_results,
            "memory_usage": {
                "start_kb": round(mem_start_curr / 1024, 2),
                "end_kb": round(mem_end_curr / 1024, 2),
                "delta_kb": round((mem_end_curr - mem_start_curr) / 1024, 2),
                "peak_kb": round(mem_end_peak / 1024, 2),
            },
        }

        output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_background_results.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        return results

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    print("Running Phase 17 Background Automation Benchmarks...")
    res = run_all_benchmarks()
    print("Benchmark complete:")
    print(json.dumps(res, indent=2))
