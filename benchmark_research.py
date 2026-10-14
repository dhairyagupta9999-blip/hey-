"""Benchmark for Hey Jev Phase 13: Research Engine.

Measures:
- Search discovery latency across topics
- Source collection and untrusted data quarantine throughput
- Evidence modeling & claim extraction latency
- Contradiction detection performance
- Synthesis report generation speed
- ResearchCache latency (cold query vs cached hit)
- Memory usage overhead
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict

import psutil

from research_engine import MockSearchProvider, ResearchCache, ResearchEngine
from browser_adapter import ResearchAgentAdapter
from agent_task import AgentTask


def run_research_benchmark() -> Dict[str, Any]:
    print("==================================================")
    print("HEY JEV PHASE 13: RESEARCH ENGINE BENCHMARK")
    print("==================================================")

    process = psutil.Process()
    initial_rss = process.memory_info().rss / (1024 * 1024)

    import shutil
    cache_dir = os.path.join(tempfile.gettempdir(), "HeyJev", "benchmark_research_cache")
    shutil.rmtree(cache_dir, ignore_errors=True)
    os.makedirs(cache_dir, exist_ok=True)

    cache = ResearchCache(cache_dir=cache_dir, ttl_seconds=3600.0)
    engine = ResearchEngine(search_provider=MockSearchProvider(), cache=cache)
    adapter = ResearchAgentAdapter(engine=engine)

    metrics: Dict[str, Any] = {}

    # 1. Cold Research Query: Hotel Research
    t0 = time.perf_counter()
    task_hotel = AgentTask(
        task_id="bench_hotel",
        goal="Search for the best beachfront hotels in Australia",
        context={"criteria": {"max_rate": 2000, "beachfront": True}},
    )
    adapter.submit_task(task_hotel)
    res_hotel = adapter.get_task_result("bench_hotel")
    t_hotel = time.perf_counter() - t0
    metrics["hotel_research_cold_ms"] = round(t_hotel * 1000, 2)
    metrics["hotel_sources_count"] = res_hotel.result.get("sources_count") if res_hotel and res_hotel.result else 0
    metrics["hotel_facts_count"] = res_hotel.result.get("facts_count") if res_hotel and res_hotel.result else 0
    print(f"1. Cold Hotel Research: {metrics['hotel_research_cold_ms']} ms ({metrics['hotel_sources_count']} sources, {metrics['hotel_facts_count']} facts)")

    # 2. Warm Research Query (Cache Hit)
    t0 = time.perf_counter()
    task_hotel_cached = AgentTask(
        task_id="bench_hotel_cached",
        goal="Search for the best beachfront hotels in Australia",
    )
    adapter.submit_task(task_hotel_cached)
    res_hotel_cached = adapter.get_task_result("bench_hotel_cached")
    t_hotel_cached = time.perf_counter() - t0
    metrics["hotel_research_cached_ms"] = round(t_hotel_cached * 1000, 2)
    metrics["cache_speedup_factor"] = round(t_hotel / max(t_hotel_cached, 0.0001), 2)
    print(f"2. Cached Hotel Research: {metrics['hotel_research_cached_ms']} ms (Speedup: {metrics['cache_speedup_factor']}x)")

    # 3. Product Comparison: Laptops
    t0 = time.perf_counter()
    task_laptop = AgentTask(
        task_id="bench_laptop",
        goal="Compare these three laptops: ThinkPad X1, Dell XPS 13, MacBook Air 15",
    )
    adapter.submit_task(task_laptop)
    res_laptop = adapter.get_task_result("bench_laptop")
    t_laptop = time.perf_counter() - t0
    metrics["laptop_comparison_ms"] = round(t_laptop * 1000, 2)
    print(f"3. Laptop Comparison: {metrics['laptop_comparison_ms']} ms")

    # 4. Error Documentation Lookup & Troubleshooting
    t0 = time.perf_counter()
    task_doc = AgentTask(
        task_id="bench_doc",
        goal="Find official documentation for FileNotFoundError exception",
    )
    adapter.submit_task(task_doc)
    res_doc = adapter.get_task_result("bench_doc")
    t_doc = time.perf_counter() - t0
    metrics["doc_lookup_ms"] = round(t_doc * 1000, 2)
    print(f"4. Error Doc Lookup: {metrics['doc_lookup_ms']} ms")

    # 5. Contradiction Detection Performance
    # Run synthetic 100-claim contradiction scan
    from research_engine import Claim, EvidenceModel
    ev = EvidenceModel()
    t0 = time.perf_counter()
    for i in range(50):
        ev.add_claim(Claim(
            statement=f"Room rate at Hotel {i} is $500",
            source_url=f"https://hotel{i}.com/a",
            subject=f"Hotel_{i}",
            attribute="rate",
            value=500,
        ))
        ev.add_claim(Claim(
            statement=f"Room rate at Hotel {i} is $400",
            source_url=f"https://hotel{i}.com/b",
            subject=f"Hotel_{i}",
            attribute="rate",
            value=400,
        ))
    t_contra = time.perf_counter() - t0
    metrics["contradiction_scan_100_claims_ms"] = round(t_contra * 1000, 2)
    metrics["detected_contradictions_count"] = len(ev.contradictions)
    print(f"5. Contradiction Scan (100 claims): {metrics['contradiction_scan_100_claims_ms']} ms (Detected: {metrics['detected_contradictions_count']})")

    # 6. Memory usage
    final_rss = process.memory_info().rss / (1024 * 1024)
    metrics["process_rss_start_mb"] = round(initial_rss, 2)
    metrics["process_rss_end_mb"] = round(final_rss, 2)
    metrics["process_rss_delta_mb"] = round(final_rss - initial_rss, 2)
    print(f"6. Memory Delta: {metrics['process_rss_delta_mb']} MB (start: {metrics['process_rss_start_mb']} MB, end: {metrics['process_rss_end_mb']} MB)")
    print("==================================================")

    # Save benchmark artifact
    artifact_path = os.path.join(os.path.dirname(__file__), "benchmark_research_results.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return metrics


if __name__ == "__main__":
    run_research_benchmark()
