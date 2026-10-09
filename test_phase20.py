"""Deterministic Unit and Integration Tests for Hey Jev Phase 20 (Computer Operator 2.0).

Verifies all acceptance criteria:
1. Accessible control discovery, attribute matching, and hierarchy/index disambiguation.
2. Correct semantic action selection (Invoke, Value, Toggle, Select, Physical Click, Clipboard).
3. Stale elements and recovery through re-resolution.
4. Safe focus manager, minimized window restoration, and explicit focus denial.
5. Per-Monitor V2 DPI initialization, established context resilience, and coordinate mapping.
6. Clipboard fallback with state preservation and restoration.
7. Phase 19 reliability integration: pre-intent logging, independent post-action verification,
   reversible undo registration, and circuit-breaker failure limits.
8. Safety policy enforcement and permission gating.
9. Phase 18 background research regression test validation.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional
import pytest

from clipboard_manager import ClipboardManager
from computer_operator_v2 import ComputerOperatorV2, OperatorV2Result, SafetyEngine
from dpi_manager import DpiAwarenessLevel, DpiManager
from focus_manager import FocusResult, SafeFocusManager
from reliability_manager import ReliabilityManager
from reliability_model import (
    ActionRecord,
    CircuitBreaker,
    ExecutionStatus,
    ReversibilityType,
    VerificationLevel,
)
from uia_model import (
    SemanticActionType,
    UiaControlType,
    UiaElementInfo,
    UiaElementLocator,
    UiaPatternType,
)
from uia_operator import (
    MockUiaBackend,
    MockUiaElement,
    MockUiaWindow,
    StaleElementError,
    UiaOperator,
)


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="heyjev_p20_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def reliability_mgr(temp_dir):
    db_path = os.path.join(temp_dir, "test_reliability.db")
    return ReliabilityManager(db_path=db_path)


@pytest.fixture
def mock_window_tree():
    """Create a rich in-memory mock window for deterministic UI testing."""
    win = MockUiaWindow(hwnd=1001, title="Test Editor")

    # Construct children: Menu, Toolbar, EditBox, CheckBox, RadioButtons, Custom Canvas
    save_btn = MockUiaElement(
        automation_id="btn_save",
        name="Save",
        control_type="Button",
        supported_patterns=[UiaPatternType.INVOKE.value],
        bounding_rect={"left": 10, "top": 10, "right": 80, "bottom": 35},
    )

    open_btn = MockUiaElement(
        automation_id="btn_open",
        name="Open",
        control_type="Button",
        supported_patterns=[UiaPatternType.INVOKE.value],
        bounding_rect={"left": 85, "top": 10, "right": 155, "bottom": 35},
    )

    toolbar = MockUiaElement(
        automation_id="main_toolbar",
        name="Main Toolbar",
        control_type="ToolBar",
        children=[save_btn, open_btn],
    )

    edit_box = MockUiaElement(
        automation_id="txt_content",
        name="Document Content",
        control_type="Edit",
        supported_patterns=[UiaPatternType.VALUE.value],
        value="Initial document text",
        bounding_rect={"left": 10, "top": 50, "right": 400, "bottom": 300},
    )

    readonly_edit = MockUiaElement(
        automation_id="txt_readonly",
        name="Readonly Note",
        control_type="Edit",
        supported_patterns=[],  # Lacks ValuePattern -> forces clipboard fallback
        value="Fixed notice",
        bounding_rect={"left": 10, "top": 310, "right": 400, "bottom": 350},
    )

    wordwrap_cb = MockUiaElement(
        automation_id="cb_wordwrap",
        name="Word Wrap",
        control_type="CheckBox",
        supported_patterns=[UiaPatternType.TOGGLE.value],
        toggle_state=0,
        bounding_rect={"left": 10, "top": 360, "right": 120, "bottom": 385},
    )

    mode_rich = MockUiaElement(
        automation_id="rb_rich",
        name="Rich Text",
        control_type="RadioButton",
        supported_patterns=[UiaPatternType.SELECTION_ITEM.value],
        is_selected=False,
    )

    mode_plain = MockUiaElement(
        automation_id="rb_plain",
        name="Plain Text",
        control_type="RadioButton",
        supported_patterns=[UiaPatternType.SELECTION_ITEM.value],
        is_selected=True,
    )

    custom_canvas = MockUiaElement(
        automation_id="canvas_view",
        name="Custom Diagram",
        control_type="Custom",
        supported_patterns=[],  # No semantic patterns -> forces physical click
        bounding_rect={"left": 410, "top": 50, "right": 700, "bottom": 350},
    )

    win.root_element.add_child(toolbar)
    win.root_element.add_child(edit_box)
    win.root_element.add_child(readonly_edit)
    win.root_element.add_child(wordwrap_cb)
    win.root_element.add_child(mode_rich)
    win.root_element.add_child(mode_plain)
    win.root_element.add_child(custom_canvas)

    return win


# =============================================================================
# 1. Accessible Control Discovery & Ambiguity Tests
# =============================================================================

class TestAccessibleControlDiscovery:

    def test_find_by_automation_id(self, mock_window_tree):
        loc = UiaElementLocator(automation_id="btn_save")
        elem = mock_window_tree.find_element(loc)
        assert elem is not None
        assert elem.get_info().name == "Save"

    def test_find_by_name(self, mock_window_tree):
        loc = UiaElementLocator(name="Word Wrap")
        elem = mock_window_tree.find_element(loc)
        assert elem is not None
        assert elem.get_info().automation_id == "cb_wordwrap"

    def test_find_by_control_type(self, mock_window_tree):
        loc = UiaElementLocator(control_type="CheckBox")
        elem = mock_window_tree.find_element(loc)
        assert elem is not None
        assert elem.get_info().automation_id == "cb_wordwrap"

    def test_parent_child_hierarchy_resolution(self, mock_window_tree):
        parent_loc = UiaElementLocator(automation_id="main_toolbar")
        child_loc = UiaElementLocator(automation_id="btn_open", parent_locator=parent_loc)
        elem = mock_window_tree.find_element(child_loc)
        assert elem is not None
        assert elem.get_info().name == "Open"

    def test_disambiguation_via_index(self, mock_window_tree):
        # Multiple buttons exist inside toolbar (Save, Open)
        loc_all = UiaElementLocator(control_type="Button")
        all_btns = mock_window_tree.find_elements(loc_all)
        assert len(all_btns) >= 2

        loc_idx0 = UiaElementLocator(control_type="Button", index=0)
        btn0 = mock_window_tree.find_element(loc_idx0)
        assert btn0.get_info().name == "Save"

        loc_idx1 = UiaElementLocator(control_type="Button", index=1)
        btn1 = mock_window_tree.find_element(loc_idx1)
        assert btn1.get_info().name == "Open"

    def test_missing_control_returns_none(self, mock_window_tree):
        loc = UiaElementLocator(automation_id="non_existent_control")
        elem = mock_window_tree.find_element(loc)
        assert elem is None


# =============================================================================
# 2. Semantic Action Selection Tests
# =============================================================================

class TestSemanticActionSelection:

    def test_button_selects_invoke(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="btn_save"))
        action_type, rationale = operator.select_semantic_action(elem, desired_action="click")
        assert action_type == SemanticActionType.INVOKE
        assert "InvokePattern" in rationale

    def test_edit_box_selects_set_value(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="txt_content"))
        action_type, rationale = operator.select_semantic_action(elem, desired_action="set_value")
        assert action_type == SemanticActionType.SET_VALUE
        assert "ValuePattern" in rationale

    def test_checkbox_selects_toggle(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="cb_wordwrap"))
        action_type, _ = operator.select_semantic_action(elem, desired_action="click")
        assert action_type == SemanticActionType.TOGGLE

    def test_radiobutton_selects_select(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="rb_rich"))
        action_type, _ = operator.select_semantic_action(elem, desired_action="click")
        assert action_type == SemanticActionType.SELECT

    def test_custom_element_lacking_patterns_selects_physical_click(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="canvas_view"))
        action_type, rationale = operator.select_semantic_action(elem, desired_action="click")
        assert action_type == SemanticActionType.PHYSICAL_CLICK
        assert "bounding-box" in rationale

    def test_edit_lacking_value_pattern_selects_clipboard_paste(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="txt_readonly"))
        action_type, rationale = operator.select_semantic_action(elem, desired_action="set_value")
        assert action_type == SemanticActionType.CLIPBOARD_PASTE
        assert "clipboard" in rationale


# =============================================================================
# 3. Stale Elements & Missing Controls
# =============================================================================

class TestStaleElementsAndMissingControls:

    def test_stale_element_raises_error(self, mock_window_tree):
        elem = mock_window_tree.find_element(UiaElementLocator(automation_id="btn_save"))
        assert elem is not None
        elem.make_stale()
        with pytest.raises(StaleElementError):
            _ = elem.get_info()

    def test_operator_find_control_recovers_from_stale_reference(self, mock_window_tree):
        operator = UiaOperator(backend=MockUiaBackend())
        loc = UiaElementLocator(automation_id="btn_save")
        elem = operator.find_control(mock_window_tree, loc)
        assert elem is not None

        # Make element stale
        elem.make_stale()
        # Refresh tree element
        fresh_elem = MockUiaElement(
            automation_id="btn_save",
            name="Save",
            control_type="Button",
            supported_patterns=[UiaPatternType.INVOKE.value],
        )
        mock_window_tree.root_element.children[0].children[0] = fresh_elem

        # Should recover and return fresh element
        recovered = operator.find_control(mock_window_tree, loc)
        assert recovered is not None
        assert recovered.is_stale() is False


# =============================================================================
# 4. Safe Focus Management & Focus Denial
# =============================================================================

class TestSafeFocusManager:

    def test_already_focused_window_succeeds_immediately(self):
        fm = SafeFocusManager(
            is_window_fn=lambda h: True,
            is_iconic_fn=lambda h: False,
            get_foreground_fn=lambda: 500,
            get_ancestor_fn=lambda h, flags: h,
            get_title_fn=lambda h: "Active App",
        )
        res = fm.activate_window(hwnd=500)
        assert res.success is True
        assert res.foreground_hwnd == 500
        assert res.denied is False

    def test_minimized_window_restored_before_activation(self):
        show_calls = []
        fg_holder = [0]

        def fake_show(h, cmd):
            show_calls.append((h, cmd))
            return True

        def fake_set_fg(h):
            fg_holder[0] = h
            return True

        fm = SafeFocusManager(
            is_window_fn=lambda h: True,
            is_iconic_fn=lambda h: True,  # Minimized
            show_window_fn=fake_show,
            set_foreground_fn=fake_set_fg,
            get_foreground_fn=lambda: fg_holder[0],
            get_ancestor_fn=lambda h, flags: h,
            get_title_fn=lambda h: "Editor",
        )
        res = fm.activate_window(hwnd=600)
        assert res.success is True
        assert res.restored is True
        assert len(show_calls) == 1
        assert show_calls[0] == (600, 9)  # SW_RESTORE is 9

    def test_focus_denial_detected_without_violent_bypass(self):
        # Simulate Windows refusing activation (foreground remains window 100)
        fm = SafeFocusManager(
            is_window_fn=lambda h: True,
            is_iconic_fn=lambda h: False,
            set_foreground_fn=lambda h: False,
            get_foreground_fn=lambda: 100,  # Remains 100 instead of requested 700
            get_ancestor_fn=lambda h, flags: 100,
            get_title_fn=lambda h: "Other App" if h == 100 else "Target App",
        )
        res = fm.activate_window(hwnd=700, timeout=0.05)
        assert res.success is False
        assert res.denied is True
        assert res.assistance_needed is True
        assert "FOCUS_DENIED" in res.error

    def test_invalid_hwnd_returns_error(self):
        fm = SafeFocusManager(is_window_fn=lambda h: False)
        res = fm.activate_window(hwnd=0)
        assert res.success is False
        assert "Invalid" in res.error


# =============================================================================
# 5. DPI Initialization & Coordinate Mapping
# =============================================================================

class TestDpiManager:

    def test_dpi_init_runs_safely(self):
        status = DpiManager.init_dpi_awareness()
        assert status["success"] is True
        assert "awareness_level" in status

    def test_scale_factor_calculation(self):
        # 96 DPI -> 1.0 scale factor
        scale = DpiManager.get_scale_factor()
        assert scale >= 1.0

    def test_logical_and_physical_coordinate_mapping(self):
        x, y = 100, 200
        px, py = DpiManager.logical_to_physical_point(x, y)
        assert px >= 100
        assert py >= 200

        lx, ly = DpiManager.physical_to_logical_point(px, py)
        assert abs(lx - x) <= 1
        assert abs(ly - y) <= 1

    def test_rect_center_calculation(self):
        rect = {"left": 50, "top": 100, "right": 150, "bottom": 200}
        cx, cy = DpiManager.rect_to_center_point(rect)
        assert cx == 100
        assert cy == 150

    def test_rect_validity_check(self):
        assert DpiManager.is_rect_valid({"left": 0, "top": 0, "right": 10, "bottom": 10}) is True
        assert DpiManager.is_rect_valid({"left": 10, "top": 10, "right": 10, "bottom": 10}) is False


# =============================================================================
# 6. Clipboard Fallback & State Preservation
# =============================================================================

class TestClipboardManager:

    def test_clipboard_set_and_get(self):
        cm = ClipboardManager()
        test_val = f"heyjev_test_{time.time()}"
        ok = cm.set_text(test_val)
        assert ok is True
        read_val = cm.get_text()
        assert read_val == test_val

    def test_paste_preserves_previous_clipboard_content(self):
        cm = ClipboardManager()
        original_clip = "Original User Clipboard Secret"
        cm.set_text(original_clip)

        # Execute paste fallback with preservation
        dispatched, err = cm.paste_into_control(hwnd=None, text="Injected Form Text", preserve_previous=True)
        assert dispatched is True
        assert err is None

        # Confirm clipboard was restored back to original user content
        restored = cm.get_text()
        assert restored == original_clip


# =============================================================================
# 7. Phase 19 Reliability Engine Integration Tests
# =============================================================================

class TestReliabilityIntegration:

    def test_operator_executes_semantic_invoke_with_wal(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)
        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            uia_operator=UiaOperator(backend=backend),
            focus_manager=SafeFocusManager(
                is_window_fn=lambda h: True,
                is_iconic_fn=lambda h: False,
                get_foreground_fn=lambda: 1001,
            ),
        )

        loc = UiaElementLocator(automation_id="btn_save")
        res = operator.execute_control_action(
            task_id="task_001",
            window_query=1001,
            locator=loc,
            action="click",
        )

        assert res.is_success is True
        assert res.action_type == SemanticActionType.INVOKE
        assert res.verified is True

        # Check WAL action record in reliability database
        history = reliability_mgr.timeline.list_records()
        assert len(history) >= 1
        last_rec = history[-1]
        assert last_rec.action_id == res.action_id
        assert last_rec.status == ExecutionStatus.SUCCEEDED
        assert last_rec.verification_state == "VERIFIED"

    def test_operator_executes_set_value_with_undo_registration(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)
        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            uia_operator=UiaOperator(backend=backend),
            focus_manager=SafeFocusManager(
                is_window_fn=lambda h: True,
                is_iconic_fn=lambda h: False,
                get_foreground_fn=lambda: 1001,
            ),
        )

        loc = UiaElementLocator(automation_id="txt_content")
        new_text = "Updated paragraph text by Jev"
        res = operator.execute_control_action(
            task_id="task_002",
            window_query=1001,
            locator=loc,
            action="set_value",
            value=new_text,
        )

        assert res.is_success is True
        assert res.action_type == SemanticActionType.SET_VALUE
        assert res.after_state.get("value") == new_text
        assert res.reversible is True
        assert res.undo_registered is True

        # Verify compensation action was attached to the action record
        history = reliability_mgr.timeline.list_records()
        last_rec = history[-1]
        assert last_rec.compensation_action is not None
        assert last_rec.compensation_action.get("restore_value") == "Initial document text"

    def test_operator_checkbox_toggle_verification(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)
        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            uia_operator=UiaOperator(backend=backend),
            focus_manager=SafeFocusManager(
                is_window_fn=lambda h: True,
                is_iconic_fn=lambda h: False,
                get_foreground_fn=lambda: 1001,
            ),
        )

        loc = UiaElementLocator(automation_id="cb_wordwrap")
        res = operator.execute_control_action(
            task_id="task_003",
            window_query=1001,
            locator=loc,
            action="click",
        )

        assert res.is_success is True
        assert res.action_type == SemanticActionType.TOGGLE
        assert res.before_state.get("toggle_state") == 0
        assert res.after_state.get("toggle_state") == 1
        assert res.verified is True

    def test_physical_click_fallback_executed_when_no_pattern(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)
        clicked_coords = []

        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            uia_operator=UiaOperator(backend=backend),
            focus_manager=SafeFocusManager(
                is_window_fn=lambda h: True,
                is_iconic_fn=lambda h: False,
                get_foreground_fn=lambda: 1001,
            ),
            physical_click_fn=lambda x, y: (clicked_coords.append((x, y)) or True),
        )

        loc = UiaElementLocator(automation_id="canvas_view")
        res = operator.execute_control_action(
            task_id="task_004",
            window_query=1001,
            locator=loc,
            action="click",
        )

        assert res.is_success is True
        assert res.action_type == SemanticActionType.PHYSICAL_CLICK
        assert res.fallback_limitation is not None
        assert "PHYSICAL_CLICK_FALLBACK" in res.fallback_limitation
        assert len(clicked_coords) == 1

    def test_verification_failure_records_failed_status_and_trips_breaker(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)

        # Make element fail to set value
        bad_edit = MockUiaElement(
            automation_id="broken_edit",
            control_type="Edit",
            supported_patterns=[UiaPatternType.VALUE.value],
            value="Old",
        )
        bad_edit.set_value = lambda val: False  # Force failure
        mock_window_tree.root_element.add_child(bad_edit)

        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            uia_operator=UiaOperator(backend=backend),
            focus_manager=SafeFocusManager(
                is_window_fn=lambda h: True,
                is_iconic_fn=lambda h: False,
                get_foreground_fn=lambda: 1001,
            ),
        )

        loc = UiaElementLocator(automation_id="broken_edit")
        # Run 3 consecutive failing attempts
        for i in range(3):
            res = operator.execute_control_action(
                task_id=f"fail_{i}",
                window_query=1001,
                locator=loc,
                action="set_value",
                value="New",
            )
            assert res.verified is False

        # 4th action should be blocked by circuit breaker
        res_blocked = operator.execute_control_action(
            task_id="fail_blocked",
            window_query=1001,
            locator=loc,
            action="set_value",
            value="New",
        )
        assert res_blocked.verified is False
        assert "CIRCUIT_BREAKER_TRIPPED" in res_blocked.error


# =============================================================================
# 8. Safety Policy & Permission Checks
# =============================================================================

class TestSafetyAndPermissions:

    def test_action_blocked_by_safety_policy(self, mock_window_tree, reliability_mgr):
        backend = MockUiaBackend()
        backend.register_window(mock_window_tree)

        # Create a mock safety engine that denies permission
        class StrictSafetyEngine:
            def check_action_allowed(self, action, args=None):
                return False

        operator = ComputerOperatorV2(
            reliability_manager=reliability_mgr,
            safety_engine=StrictSafetyEngine(),
            uia_operator=UiaOperator(backend=backend),
        )

        loc = UiaElementLocator(automation_id="btn_save")
        res = operator.execute_control_action(
            task_id="task_denied",
            window_query=1001,
            locator=loc,
            action="click",
        )
        assert res.is_success is False
        assert "PERMISSION_DENIED" in res.error


# =============================================================================
# 9. Phase 18 Background Research Memory Regression
# =============================================================================

class TestPhase18BackgroundResearchMemoryRegression:

    def test_background_research_creates_distinct_memory_and_completes(self, temp_dir):
        """Phase 18 regression: Background research task must complete and create a distinct memory record."""
        from memory_model import EpistemicStatus, MemoryRecord, MemorySource, MemoryType
        from memory_store import SqliteMemoryStore
        from notification_manager import NotificationLevel, NotificationManager
        from provider_gateway import ProviderGateway
        from provider_adapters import MockProviderFast
        from swarm_coordinator import SwarmCoordinator
        from background_task_manager import BackgroundTaskManager
        from background_task_model import BackgroundTaskDefinition, BackgroundTaskPriority

        mem_db = os.path.join(temp_dir, "mem_p20.db")
        memory_store = SqliteMemoryStore(db_path=mem_db)

        # Baseline knowledge with distinct content that cannot satisfy research assertion
        base_rec = MemoryRecord(
            type=MemoryType.RESEARCH_FACT,
            content="Baseline network latency was 15ms",
            summary="Network metric baseline",
            epistemic_status=EpistemicStatus.FACT,
        )
        memory_store.add(base_rec)
        assert memory_store.count() == 1

        notifier = NotificationManager()
        gateway = ProviderGateway()
        gateway.register_transport("openai_compatible", MockProviderFast("openai_compatible"))
        coord = SwarmCoordinator(
            gateway=gateway,
            memory_store=memory_store,
            notification_manager=notifier,
        )

        bg_db = os.path.join(temp_dir, "bg_p20.db")
        bg_mgr = BackgroundTaskManager(
            db_path=bg_db,
            task_executor=coord.handle_background_task,
            notification_manager=notifier,
        )

        bg_task = BackgroundTaskDefinition(
            task_id="bg_research_p20",
            name="Research Swarm Task",
            action_type="swarm",
            action_payload={
                "goal": "Research new distributed compute benchmarks",
                "strategy": "PARALLEL",
            },
            priority=BackgroundTaskPriority.NORMAL,
        )
        bg_mgr.register_task(bg_task)

        ran = bg_mgr.run_now("bg_research_p20")
        assert ran is True

        for _ in range(50):
            if len(notifier.get_history()) >= 1:
                break
            time.sleep(0.05)

        # Assert memory count increased from 1 to 2
        assert memory_store.count() == 2
        records = memory_store.list()
        new_records = [r for r in records if r.memory_id != base_rec.memory_id]
        assert len(new_records) == 1
        new_rec = new_records[0]
        assert new_rec.type == MemoryType.MISSION_SUMMARY
        assert new_rec.source == MemorySource.AGENT
        assert "Research new distributed compute benchmarks" in new_rec.summary

        # Assert notification reflects COMPLETED, not FAILURE
        history = notifier.get_history()
        assert len(history) >= 1
        assert any(n.level == NotificationLevel.NORMAL and ("COMPLETED" in n.message or "finished" in n.message) for n in history)
        assert not any(n.level == NotificationLevel.HIGH for n in history)
        bg_mgr.stop()

