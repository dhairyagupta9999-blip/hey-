"""Benchmark for Hey Jev Phase 13: Browser Operator.

Measures:
- Browser backend launch latency
- Navigation latency
- DOM observation and semantic lookup latency
- Screenshot capture latency
- Tab lifecycle latency (open, switch, close)
- Memory usage overhead
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any, Dict

import psutil

from browser_session import BrowserSessionManager, MockBrowserBackend, CDPBrowserBackend
from browser_adapter import BrowserAgent
from agent_task import AgentTask, TaskPermission


def run_browser_benchmark(use_real_browser: bool = False) -> Dict[str, Any]:
    print("==================================================")
    print("HEY JEV PHASE 13: BROWSER OPERATOR BENCHMARK")
    print(f"Backend mode: {'Real Chrome/Edge CDP' if use_real_browser else 'MockBrowserBackend (deterministic)'}")
    print("==================================================")

    process = psutil.Process()
    initial_rss = process.memory_info().rss / (1024 * 1024)

    sandbox_dir = os.path.join(tempfile.gettempdir(), "HeyJev", "benchmark_browser")
    os.makedirs(sandbox_dir, exist_ok=True)

    backend = CDPBrowserBackend() if use_real_browser else MockBrowserBackend()
    manager = BrowserSessionManager(backend=backend, profile_dir=os.path.join(sandbox_dir, "profile"))
    agent = BrowserAgent(session_manager=manager, sandbox_dir=sandbox_dir)

    metrics: Dict[str, Any] = {}

    # 1. Startup latency
    t0 = time.perf_counter()
    ok_launch = manager.start(headless=True)
    t_launch = time.perf_counter() - t0
    metrics["launch_latency_ms"] = round(t_launch * 1000, 2)
    metrics["launch_success"] = ok_launch
    print(f"1. Browser Launch Latency: {metrics['launch_latency_ms']} ms (success={ok_launch})")

    # 2. Navigation latency
    t0 = time.perf_counter()
    nav_task = AgentTask(
        task_id="bench_nav",
        goal="Navigate to https://example.com and read website",
        context={"url": "https://example.com", "headless": True},
    )
    agent.submit_task(nav_task)
    res_nav = agent.get_task_result("bench_nav")
    t_nav = time.perf_counter() - t0
    metrics["navigation_latency_ms"] = round(t_nav * 1000, 2)
    metrics["navigation_success"] = res_nav.is_success if res_nav else False
    print(f"2. Navigation Latency: {metrics['navigation_latency_ms']} ms (success={metrics['navigation_success']})")

    # 3. Tab operations latency
    t0 = time.perf_counter()
    tab_task = AgentTask(
        task_id="bench_tab",
        goal="Open new tab with https://example.org",
        context={"url": "https://example.org"},
    )
    agent.submit_task(tab_task)
    res_tab = agent.get_task_result("bench_tab")
    t_tab = time.perf_counter() - t0
    metrics["tab_open_latency_ms"] = round(t_tab * 1000, 2)
    print(f"3. Tab Open Latency: {metrics['tab_open_latency_ms']} ms")

    # 4. Form filling latency
    t0 = time.perf_counter()
    form_task = AgentTask(
        task_id="bench_form",
        goal="Fill form and submit",
        context={"form_data": {"username": "jev_user", "search": "benchmark"}},
    )
    agent.submit_task(form_task)
    res_form = agent.get_task_result("bench_form")
    t_form = time.perf_counter() - t0
    metrics["form_fill_latency_ms"] = round(t_form * 1000, 2)
    print(f"4. Form Fill Latency: {metrics['form_fill_latency_ms']} ms")

    # 5. Screenshot capture latency
    screenshot_path = os.path.join(sandbox_dir, "bench_screenshot.png")
    t0 = time.perf_counter()
    ss_task = AgentTask(
        task_id="bench_ss",
        goal="Capture page screenshot",
        context={"output_path": screenshot_path},
    )
    agent.submit_task(ss_task)
    res_ss = agent.get_task_result("bench_ss")
    t_ss = time.perf_counter() - t0
    metrics["screenshot_latency_ms"] = round(t_ss * 1000, 2)
    metrics["screenshot_success"] = os.path.exists(screenshot_path) and os.path.getsize(screenshot_path) > 0
    print(f"5. Screenshot Latency: {metrics['screenshot_latency_ms']} ms (file_size={os.path.getsize(screenshot_path) if metrics['screenshot_success'] else 0} B)")

    # 6. Cleanup & Memory Overhead
    manager.stop()
    final_rss = process.memory_info().rss / (1024 * 1024)
    metrics["process_rss_start_mb"] = round(initial_rss, 2)
    metrics["process_rss_end_mb"] = round(final_rss, 2)
    metrics["process_rss_delta_mb"] = round(final_rss - initial_rss, 2)
    print(f"6. Memory Delta: {metrics['process_rss_delta_mb']} MB (start: {metrics['process_rss_start_mb']} MB, end: {metrics['process_rss_end_mb']} MB)")
    print("==================================================")

    # Save benchmark artifact
    artifact_path = os.path.join(os.path.dirname(__file__), "benchmark_browser_results.json")
    with open(artifact_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    return metrics


if __name__ == "__main__":
    use_real = "--real" in sys.argv
    run_browser_benchmark(use_real_browser=use_real)
