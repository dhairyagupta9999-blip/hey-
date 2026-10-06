"""Phase 15: Skill System & Dynamic Tool Registry Benchmark Suite for Hey Jev.

Measures:
1. Tool Registry lookup latency (by tool_id, capability, permission, risk)
2. Skill Matching latency (natural-language goal routing)
3. Tool Matching latency (multi-factor quality scoring)
4. Permission and risk checking latency
5. Tool execution overhead (gating, dispatch, verification)
6. Skill workflow execution overhead: 1-tool, 3-tool, and 5-tool compositions
7. Memory delta and allocation overhead
"""
from __future__ import annotations

import json
import os
import sys
import time
import tracemalloc
from typing import Any, Dict, List

# Ensure hey-jev root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from agent_task import TaskPermission
from builtin_skills import get_builtin_skills, register_builtin_skills
from event_bus import EventBus
from skill_executor import SkillExecutor
from skill_matcher import SkillMatcher
from skill_model import SkillDefinition, SkillStep, SkillTrustLevel
from skill_registry import SkillRegistry
from tool_executor import ToolExecutor
from tool_matcher import ToolMatcher
from tool_model import RiskLevel, ToolDefinition, ToolHealth, ToolResult
from tool_registry import ToolRegistry
from tool_scanner import ToolScanner


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
    print("  HEY JEV - PHASE 15: SKILL SYSTEM & DYNAMIC TOOL REGISTRY BENCHMARK")
    print("=" * 72)
    print(f"Platform: {sys.platform} | Python: {sys.version.split()[0]}")

    tracemalloc.start()
    mem_before, _ = tracemalloc.get_traced_memory()

    # 1. Setup registries and scanners
    tool_reg = ToolRegistry()
    skill_reg = SkillRegistry()
    event_bus = EventBus()
    tool_scanner = ToolScanner(registry=tool_reg)
    tool_scanner.scan_and_register_all()
    register_builtin_skills(registry=skill_reg)

    tool_matcher = ToolMatcher(registry=tool_reg)
    skill_matcher = SkillMatcher(registry=skill_reg)
    tool_executor = ToolExecutor(registry=tool_reg, event_bus=event_bus)
    skill_executor = SkillExecutor(
        tool_registry=tool_reg,
        tool_executor=tool_executor,
        event_bus=event_bus,
    )

    results: Dict[str, Any] = {
        "metadata": {
            "tools_registered": len(tool_reg),
            "skills_registered": len(skill_reg),
            "platform": sys.platform,
        },
        "benchmarks": {},
    }

    # -----------------------------------------------------------------
    # Benchmark 1: Tool Registry Lookup Latency
    # -----------------------------------------------------------------
    lookup_iters = 1000
    latencies_get: List[float] = []
    latencies_cap: List[float] = []

    for _ in range(lookup_iters):
        t0 = time.perf_counter()
        tool_reg.get("filesystem.list_directory")
        latencies_get.append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        tool_reg.find_by_capability("web_search")
        latencies_cap.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["tool_registry_get"] = calc_percentiles(latencies_get)
    results["benchmarks"]["tool_registry_find_capability"] = calc_percentiles(latencies_cap)

    print(f"\n[1] Tool Registry Lookups ({lookup_iters} iters):")
    print(f"    - ID lookup:   p50={results['benchmarks']['tool_registry_get']['p50']} ms | p95={results['benchmarks']['tool_registry_get']['p95']} ms")
    print(f"    - Cap lookup:  p50={results['benchmarks']['tool_registry_find_capability']['p50']} ms | p95={results['benchmarks']['tool_registry_find_capability']['p95']} ms")

    # -----------------------------------------------------------------
    # Benchmark 2: Tool Quality Scoring & Matching Latency
    # -----------------------------------------------------------------
    match_iters = 500
    latencies_tool_match: List[float] = []

    for _ in range(match_iters):
        t0 = time.perf_counter()
        tool_matcher.match("web_search", caller_permissions=[TaskPermission.NETWORK_ACCESS])
        latencies_tool_match.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["tool_matcher"] = calc_percentiles(latencies_tool_match)
    print(f"\n[2] Tool Matcher ({match_iters} iters):")
    print(f"    - Multi-factor score: p50={results['benchmarks']['tool_matcher']['p50']} ms | p95={results['benchmarks']['tool_matcher']['p95']} ms")

    # -----------------------------------------------------------------
    # Benchmark 3: Skill Matching Latency (Natural-Language Goal Routing)
    # -----------------------------------------------------------------
    skill_match_iters = 500
    test_goals = [
        "Open the fourth picture in my folder",
        "Research the latest news on AI hardware",
        "Organize my messy Downloads directory",
        "Set my wallpaper to beach.jpg",
    ]
    latencies_skill_match: List[float] = []

    for i in range(skill_match_iters):
        goal = test_goals[i % len(test_goals)]
        t0 = time.perf_counter()
        skill_matcher.match(goal)
        latencies_skill_match.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["skill_matcher"] = calc_percentiles(latencies_skill_match)
    print(f"\n[3] Skill Matcher ({skill_match_iters} iters):")
    print(f"    - NL Goal routing:   p50={results['benchmarks']['skill_matcher']['p50']} ms | p95={results['benchmarks']['skill_matcher']['p95']} ms")

    # -----------------------------------------------------------------
    # Benchmark 4: Permission & Risk Gating Overhead
    # -----------------------------------------------------------------
    perm_iters = 1000
    t_tool = tool_reg.get("filesystem.list_directory")
    assert t_tool is not None
    latencies_perm: List[float] = []

    for _ in range(perm_iters):
        t0 = time.perf_counter()
        tool_executor.check_permission(t_tool, granted_permissions=[TaskPermission.READ_FILES])
        latencies_perm.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["permission_check"] = calc_percentiles(latencies_perm)
    print(f"\n[4] Permission Gating ({perm_iters} iters):")
    print(f"    - Policy evaluation: p50={results['benchmarks']['permission_check']['p50']} ms | p95={results['benchmarks']['permission_check']['p95']} ms")

    # -----------------------------------------------------------------
    # Benchmark 5: Tool Execution Overhead
    # -----------------------------------------------------------------
    exec_iters = 500
    latencies_exec: List[float] = []

    for _ in range(exec_iters):
        t0 = time.perf_counter()
        tool_executor.execute(
            tool_id="windows.get_active_window",
            inputs={},
            granted_permissions=[],
        )
        latencies_exec.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["tool_execution_overhead"] = calc_percentiles(latencies_exec)
    print(f"\n[5] Tool Execution ({exec_iters} iters):")
    print(f"    - Direct dispatch:   p50={results['benchmarks']['tool_execution_overhead']['p50']} ms | p95={results['benchmarks']['tool_execution_overhead']['p95']} ms")

    # -----------------------------------------------------------------
    # Benchmark 6: Multi-Tool Workflow Compositions (1-tool, 3-tool, 5-tool)
    # -----------------------------------------------------------------
    # Setup test tools for workflow benchmarks
    def make_fast_tool(tid: str, cap: str) -> ToolDefinition:
        def h(**kwargs: Any) -> ToolResult:
            return ToolResult(tool_id=tid, status="SUCCESS", output={"val": kwargs.get("in_val", 1) + 1})

        td = ToolDefinition(
            tool_id=tid,
            name=tid,
            description=f"Fast benchmark tool {tid}",
            capabilities=[cap],
            input_schema={"type": "object", "properties": {"in_val": {"type": "integer"}}},
        )
        tool_reg.register(td, handler=h)
        return td

    t1 = make_fast_tool("bench.step1", "bench1")
    t2 = make_fast_tool("bench.step2", "bench2")
    t3 = make_fast_tool("bench.step3", "bench3")
    t4 = make_fast_tool("bench.step4", "bench4")
    t5 = make_fast_tool("bench.step5", "bench5")

    skill_1step = SkillDefinition(
        skill_id="bench.skill_1",
        name="1-Tool Skill",
        description="One step benchmark skill",
        workflow=[{"step_id": "s1", "tool_id": "bench.step1", "inputs": {"in_val": 10}}],
    )
    skill_3step = SkillDefinition(
        skill_id="bench.skill_3",
        name="3-Tool Skill",
        description="Three step benchmark skill",
        workflow=[
            {"step_id": "s1", "tool_id": "bench.step1", "inputs": {"in_val": 10}},
            {"step_id": "s2", "tool_id": "bench.step2", "inputs": {"in_val": 20}},
            {"step_id": "s3", "tool_id": "bench.step3", "inputs": {"in_val": 30}},
        ],
    )
    skill_5step = SkillDefinition(
        skill_id="bench.skill_5",
        name="5-Tool Skill",
        description="Five step benchmark skill",
        workflow=[
            {"step_id": "s1", "tool_id": "bench.step1", "inputs": {"in_val": 10}},
            {"step_id": "s2", "tool_id": "bench.step2", "inputs": {"in_val": 20}},
            {"step_id": "s3", "tool_id": "bench.step3", "inputs": {"in_val": 30}},
            {"step_id": "s4", "tool_id": "bench.step4", "inputs": {"in_val": 40}},
            {"step_id": "s5", "tool_id": "bench.step5", "inputs": {"in_val": 50}},
        ],
    )
    skill_reg.register(skill_1step)
    skill_reg.register(skill_3step)
    skill_reg.register(skill_5step)

    wf_iters = 200
    lat_1step: List[float] = []
    lat_3step: List[float] = []
    lat_5step: List[float] = []

    for _ in range(wf_iters):
        t0 = time.perf_counter()
        skill_executor.execute_skill(skill_1step, inputs={})
        lat_1step.append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        skill_executor.execute_skill(skill_3step, inputs={})
        lat_3step.append((time.perf_counter() - t0) * 1000.0)

        t0 = time.perf_counter()
        skill_executor.execute_skill(skill_5step, inputs={})
        lat_5step.append((time.perf_counter() - t0) * 1000.0)

    results["benchmarks"]["workflow_1_tool"] = calc_percentiles(lat_1step)
    results["benchmarks"]["workflow_3_tool"] = calc_percentiles(lat_3step)
    results["benchmarks"]["workflow_5_tool"] = calc_percentiles(lat_5step)

    print(f"\n[6] Skill Workflow Execution ({wf_iters} iters):")
    print(f"    - 1-tool workflow: p50={results['benchmarks']['workflow_1_tool']['p50']} ms | p95={results['benchmarks']['workflow_1_tool']['p95']} ms")
    print(f"    - 3-tool workflow: p50={results['benchmarks']['workflow_3_tool']['p50']} ms | p95={results['benchmarks']['workflow_3_tool']['p95']} ms")
    print(f"    - 5-tool workflow: p50={results['benchmarks']['workflow_5_tool']['p50']} ms | p95={results['benchmarks']['workflow_5_tool']['p95']} ms")

    # -----------------------------------------------------------------
    # Memory Delta
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

    print(f"\n[7] Memory Profile:")
    print(f"    - Delta: {results['memory']['delta_kb']} KB")
    print(f"    - Peak:  {results['memory']['peak_kb']} KB")

    out_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "benchmark_skills_results.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults successfully written to: {out_file}")
    print("=" * 72)
    return results


if __name__ == "__main__":
    run_benchmarks()
