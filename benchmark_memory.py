"""Phase 16: Persistent Memory & Project Awareness Benchmark Suite for Hey Jev.

Measures:
1. Memory insertion latency (SQLite WAL)
2. Memory ID lookup latency
3. Keyword search latency
4. Structured filtering latency
5. Context assembly latency (ContextBuilder)
6. Bounded project discovery latency
7. Safe read-only Git inspection latency
8. Memory consolidation latency
9. Memory allocation footprint (tracemalloc delta & peak)
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

from agent_task import AgentTask, TaskPermission
from context_builder import ContextBuilder
from context_snapshot import ContextSnapshot
from git_inspector import GitInspector
from memory_model import (
    EpistemicStatus,
    MemoryRecord,
    MemorySensitivity,
    MemorySource,
    MemoryType,
)
from memory_store import SqliteMemoryStore
from project_model import Project
from project_registry import ProjectRegistry
from project_scanner import ProjectScanner


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


def run_benchmarks() -> Dict[str, Any]:
    print("=" * 72)
    print("  HEY JEV - PHASE 16: PERSISTENT MEMORY & PROJECT AWARENESS BENCHMARK")
    print("=" * 72)
    print(f"Platform: {sys.platform} | Python: {sys.version.split()[0]}")

    tracemalloc.start()
    mem_before, _ = tracemalloc.get_traced_memory()

    temp_dir = tempfile.mkdtemp(prefix="heyjev_bench_p16_")
    db_path = os.path.join(temp_dir, "benchmark_memory.db")
    proj_db_path = os.path.join(temp_dir, "benchmark_projects.db")

    try:
        store = SqliteMemoryStore(db_path=db_path)
        proj_reg = ProjectRegistry(db_path=proj_db_path)
        git_insp = GitInspector()
        ctx_builder = ContextBuilder(memory_store=store, git_inspector=git_insp)
        scanner = ProjectScanner(registry=proj_reg)

        results: Dict[str, Any] = {
            "metadata": {
                "platform": sys.platform,
                "db_path": db_path,
            },
            "benchmarks": {},
        }

        # -----------------------------------------------------------------
        # Benchmark 1: Memory Insertion Latency
        # -----------------------------------------------------------------
        insert_iters = 100
        lat_insert: List[float] = []

        for i in range(insert_iters):
            rec = MemoryRecord(
                type=MemoryType.PROJECT_DECISION if i % 2 == 0 else MemoryType.USER_FACT,
                content=f"Benchmark decision index {i}: configured database shard {i % 5}",
                summary=f"Shard configuration {i}",
                project_id="proj_heyjev",
                importance=0.6,
                tags=["benchmark", f"tag_{i % 10}"],
            )
            t0 = time.perf_counter()
            store.add(rec)
            lat_insert.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["memory_insert"] = calc_percentiles(lat_insert)
        print(f"\n[1] Memory Insert ({insert_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['memory_insert']['p50']} ms | p95={results['benchmarks']['memory_insert']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 2: Memory ID Lookup Latency
        # -----------------------------------------------------------------
        lookup_iters = 200
        sample_records = store.list(limit=20)
        sample_ids = [r.memory_id for r in sample_records]
        lat_lookup: List[float] = []

        for i in range(lookup_iters):
            mid = sample_ids[i % len(sample_ids)]
            t0 = time.perf_counter()
            store.get(mid)
            lat_lookup.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["memory_get"] = calc_percentiles(lat_lookup)
        print(f"\n[2] Memory Primary Key Lookup ({lookup_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['memory_get']['p50']} ms | p95={results['benchmarks']['memory_get']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 3: Keyword Search Latency
        # -----------------------------------------------------------------
        search_iters = 100
        queries = ["database shard", "decision index", "configuration", "tag_3"]
        lat_search: List[float] = []

        for i in range(search_iters):
            q = queries[i % len(queries)]
            t0 = time.perf_counter()
            store.search(q, limit=10)
            lat_search.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["keyword_search"] = calc_percentiles(lat_search)
        print(f"\n[3] Keyword Search ({search_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['keyword_search']['p50']} ms | p95={results['benchmarks']['keyword_search']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 4: Structured Filter Latency
        # -----------------------------------------------------------------
        filter_iters = 100
        lat_filter: List[float] = []

        for _ in range(filter_iters):
            t0 = time.perf_counter()
            store.list(filters={"project_id": "proj_heyjev", "type": MemoryType.PROJECT_DECISION}, limit=20)
            lat_filter.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["structured_filter"] = calc_percentiles(lat_filter)
        print(f"\n[4] Structured Filter ({filter_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['structured_filter']['p50']} ms | p95={results['benchmarks']['structured_filter']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 5: Context Assembly Latency
        # -----------------------------------------------------------------
        ctx_iters = 50
        test_proj = Project(
            project_id="proj_heyjev",
            name="Hey Jev",
            root_path="",
            languages=["Python"],
            frameworks=["Pytest"],
        )
        test_task = AgentTask(goal="Configure database caching and indexing")
        test_env = ContextSnapshot(active_app="Code", window_title="hey-jev - Visual Studio Code")
        lat_ctx: List[float] = []

        for _ in range(ctx_iters):
            t0 = time.perf_counter()
            ctx_dict = ctx_builder.build_context(
                task=test_task,
                project=test_proj,
                env_snapshot=test_env,
                caller_permissions=[TaskPermission.READ_FILES],
            )
            ctx_builder.format_prompt_context(ctx_dict)
            lat_ctx.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["context_assembly"] = calc_percentiles(lat_ctx)
        print(f"\n[5] Context Assembly ({ctx_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['context_assembly']['p50']} ms | p95={results['benchmarks']['context_assembly']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 6: Bounded Project Discovery Latency
        # -----------------------------------------------------------------
        # Setup mock project tree in temp dir
        scan_root = os.path.join(temp_dir, "scan_workspace")
        for p_name in ["project_alpha", "project_beta", "project_gamma"]:
            p_dir = os.path.join(scan_root, p_name)
            os.makedirs(p_dir, exist_ok=True)
            with open(os.path.join(p_dir, "pyproject.toml"), "w") as pf:
                pf.write("[project]\nname = 'test'\n")

        scan_iters = 20
        lat_scan: List[float] = []

        for _ in range(scan_iters):
            t0 = time.perf_counter()
            scanner.scan_directory(scan_root, max_depth=2)
            lat_scan.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["project_discovery"] = calc_percentiles(lat_scan)
        print(f"\n[6] Project Discovery ({scan_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['project_discovery']['p50']} ms | p95={results['benchmarks']['project_discovery']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 7: Read-Only Git Inspection Latency
        # -----------------------------------------------------------------
        repo_root = os.path.dirname(os.path.abspath(__file__))
        git_iters = 5
        lat_git: List[float] = []

        for _ in range(git_iters):
            t0 = time.perf_counter()
            git_insp.inspect(repo_root)
            lat_git.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["git_inspection"] = calc_percentiles(lat_git)
        print(f"\n[7] Read-Only Git Inspection ({git_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['git_inspection']['p50']} ms | p95={results['benchmarks']['git_inspection']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Benchmark 8: Memory Consolidation Latency
        # -----------------------------------------------------------------
        consol_iters = 20
        lat_consol: List[float] = []

        for _ in range(consol_iters):
            recs_to_merge = sample_records[:3]
            t0 = time.perf_counter()
            store.consolidate(
                records=recs_to_merge,
                consolidated_content="Consolidated shard and database architecture overview",
                summary="Consolidated database architecture",
            )
            lat_consol.append((time.perf_counter() - t0) * 1000.0)

        results["benchmarks"]["memory_consolidation"] = calc_percentiles(lat_consol)
        print(f"\n[8] Memory Consolidation ({consol_iters} iters):", flush=True)
        print(f"    - p50={results['benchmarks']['memory_consolidation']['p50']} ms | p95={results['benchmarks']['memory_consolidation']['p95']} ms", flush=True)

        # -----------------------------------------------------------------
        # Memory Profile
        # -----------------------------------------------------------------
        mem_after, mem_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        results["memory"] = {
            "start_bytes": mem_before,
            "after_bytes": mem_after,
            "peak_bytes": mem_peak,
            "delta_kb": round((mem_after - mem_before) / 1024.0, 2),
            "peak_kb": round(mem_peak / 1024.0, 2),
        }

        print(f"\n[9] Memory Profile:")
        print(f"    - Delta: {results['memory']['delta_kb']} KB")
        print(f"    - Peak:  {results['memory']['peak_kb']} KB")

        out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_memory_results.json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)

        print(f"\nResults successfully written to: {out_file}")
        print("=" * 72)
        return results

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    run_benchmarks()
