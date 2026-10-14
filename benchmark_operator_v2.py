"""Quantitative Performance Benchmark for Computer Operator 2.0 (Phase 20).

Benchmarks:
- DPI-Awareness initialization & coordinate translation
- SafeFocusManager activation and verification
- ClipboardManager read/write and preservation
- UiaOperator semantic control resolution & pattern selection
- ComputerOperatorV2 full end-to-end execution loop (with WAL + verification)
- Comparison against Phase 12 tree traversal
- Peak memory delta
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from typing import Any, Dict, List
import psutil

from clipboard_manager import ClipboardManager
from computer_operator_v2 import ComputerOperatorV2
from dpi_manager import DpiManager
from focus_manager import SafeFocusManager
from reliability_manager import ReliabilityManager
from uia_model import UiaElementLocator, UiaPatternType
from uia_operator import MockUiaBackend, MockUiaElement, MockUiaWindow, UiaOperator
from windows_computer_tools import WindowsComputerTools


def run_benchmarks() -> Dict[str, Any]:
    process = psutil.Process()
    ram_before = process.memory_info().rss / (1024 * 1024)

    results: Dict[str, Any] = {"benchmarks": {}}

    # 1. DPI Manager
    durations = []
    for _ in range(100):
        t0 = time.perf_counter()
        _ = DpiManager.init_dpi_awareness()
        _ = DpiManager.logical_to_physical_point(150, 250)
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["dpi_init_and_scaling"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    # 2. Safe Focus Manager
    fm = SafeFocusManager(
        is_window_fn=lambda h: True,
        is_iconic_fn=lambda h: False,
        get_foreground_fn=lambda: 1001,
        get_ancestor_fn=lambda h, f: 1001,
    )
    durations = []
    for _ in range(100):
        t0 = time.perf_counter()
        _ = fm.activate_window(1001)
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["safe_focus_verification"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    # 3. Clipboard Operations
    cm = ClipboardManager()
    durations = []
    for _ in range(50):
        t0 = time.perf_counter()
        cm.set_text("bench_payload")
        _ = cm.get_text()
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["clipboard_rw_cycle"] = {
        "p50_ms": round(durations[25], 4),
        "p95_ms": round(durations[47], 4),
    }

    # Setup Mock Window Tree for Operator Benchmarks
    win = MockUiaWindow(hwnd=1001, title="Editor Window")
    btn = MockUiaElement(automation_id="save_btn", name="Save", control_type="Button", supported_patterns=[UiaPatternType.INVOKE.value])
    edit = MockUiaElement(automation_id="text_edit", name="Document", control_type="Edit", supported_patterns=[UiaPatternType.VALUE.value], value="Hello")
    win.root_element.add_child(btn)
    win.root_element.add_child(edit)

    backend = MockUiaBackend()
    backend.register_window(win)
    temp_dir = tempfile.mkdtemp(prefix="bench_op2_")
    rel_mgr = ReliabilityManager(db_path=os.path.join(temp_dir, "bench.db"))

    operator_v2 = ComputerOperatorV2(
        reliability_manager=rel_mgr,
        uia_operator=UiaOperator(backend=backend),
        focus_manager=fm,
    )

    # 4. Semantic Element Resolution
    durations = []
    loc = UiaElementLocator(automation_id="save_btn")
    for _ in range(100):
        t0 = time.perf_counter()
        el = operator_v2.uia.find_control(win, loc)
        _ = operator_v2.uia.select_semantic_action(el, "click")
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["semantic_control_resolution"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    # 5. Full End-to-End Operator 2.0 Action (WAL Intent + Semantic Invoke + Verification + WAL Outcome)
    durations = []
    for i in range(100):
        t0 = time.perf_counter()
        res = operator_v2.execute_control_action(
            task_id=f"bench_task_{i}",
            window_query=1001,
            locator=loc,
            action="click",
        )
        assert res.verified is True
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["e2e_semantic_action_with_wal"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    # 6. Reversible SetValue with Compensation Registration
    durations = []
    edit_loc = UiaElementLocator(automation_id="text_edit")
    for i in range(100):
        t0 = time.perf_counter()
        res = operator_v2.execute_control_action(
            task_id=f"bench_edit_{i}",
            window_query=1001,
            locator=edit_loc,
            action="set_value",
            value=f"Text_{i}",
        )
        assert res.verified is True
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["e2e_set_value_with_undo_reg"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    # 7. Comparison: Phase 12 Coordinate / Tree Search
    p12_tools = WindowsComputerTools()
    durations = []
    tree_mock = {"name": "Root", "children": [{"name": "Child1", "automation_id": "c1"}, {"name": "Child2", "automation_id": "c2"}]}
    for _ in range(100):
        t0 = time.perf_counter()
        # Phase 12 tree locator search
        _ = p12_tools.find_ui_element({"automation_id": "c2"})
        durations.append((time.perf_counter() - t0) * 1000.0)
    durations.sort()
    results["benchmarks"]["phase12_find_ui_element"] = {
        "p50_ms": round(durations[50], 4),
        "p95_ms": round(durations[95], 4),
    }

    ram_after = process.memory_info().rss / (1024 * 1024)
    results["memory"] = {
        "ram_before_mb": round(ram_before, 2),
        "ram_after_mb": round(ram_after, 2),
        "ram_delta_mb": round(ram_after - ram_before, 2),
    }

    shutil.rmtree(temp_dir, ignore_errors=True)

    with open("benchmark_operator_v2_results.json", "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    res = run_benchmarks()
    print(json.dumps(res, indent=2))
