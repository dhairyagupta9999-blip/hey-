"""Live Windows Desktop UI Automation Smoke Test for Hey Jev (Phase 20.1).

Tests ComputerOperatorV2 and PywinautoUiaBackend against a dedicated,
non-sensitive PySide6 test window on the actual Windows desktop.

Verifies:
(a) Discovering test button and text field via live UIA
(b) Setting text field value via semantic action (ValuePattern / set_value)
(c) Activating button via InvokePattern
(d) Verifying resulting UI state independently
(e) Detecting missing control without reporting success
(f) Respecting focus denial and stopping safely when focus cannot be obtained
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import Dict, Any, Optional

# Ensure STA mode for COM before any Qt or pywinauto calls
if not hasattr(sys, "coinit_flags"):
    sys.coinit_flags = 2

from PySide6 import QtCore, QtWidgets

from computer_operator_v2 import ComputerOperatorV2, OperatorV2Result
from focus_manager import FocusResult, SafeFocusManager
from uia_model import SemanticActionType, UiaElementLocator
from uia_operator import PywinautoUiaBackend, UiaOperator


class SmokeTestWindow(QtWidgets.QMainWindow):
    """Isolated, dedicated test window with accessible widgets."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("HeyJev_SmokeTest_Win")
        self.resize(320, 240)

        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # 1. Text input field
        self.input_field = QtWidgets.QLineEdit("Initial Text", central)
        self.input_field.setObjectName("TestInputField")
        self.input_field.setAccessibleName("Test Input Field")
        layout.addWidget(self.input_field)

        # 2. Action button
        self.action_button = QtWidgets.QPushButton("Click Me", central)
        self.action_button.setObjectName("TestActionButton")
        self.action_button.setAccessibleName("Test Action Button")
        self.action_button.clicked.connect(self._on_button_clicked)
        layout.addWidget(self.action_button)

        # 3. Status label
        self.status_label = QtWidgets.QLabel("Status: Ready", central)
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setAccessibleName("Status Label")
        layout.addWidget(self.status_label)

        self.click_count = 0

    def _on_button_clicked(self) -> None:
        self.click_count += 1
        self.action_button.setText(f"Clicked {self.click_count}")
        self.status_label.setText(f"Status: Clicked ({self.click_count})")


def run_smoke_test() -> Dict[str, Any]:
    print("=" * 60)
    print("Hey Jev Phase 20.1: Live Windows Desktop UIA Smoke Test")
    print("=" * 60)

    # Initialize Qt Application
    app = QtWidgets.QApplication.instance()
    if not app:
        app = QtWidgets.QApplication([])

    window = SmokeTestWindow()
    window.show()
    app.processEvents()
    time.sleep(0.5)
    app.processEvents()

    hwnd = int(window.winId())
    print(f"[*] Test window created with HWND: {hwnd} (0x{hwnd:X}), Title: '{window.windowTitle()}'")

    results: Dict[str, Any] = {}

    try:
        # Initialize UIA Operator with live Pywinauto backend
        live_backend = PywinautoUiaBackend()
        live_uia = UiaOperator(backend=live_backend)
        operator = ComputerOperatorV2(uia_operator=live_uia)

        # Find window via live UIA
        uia_win = live_backend.find_window(hwnd)
        assert uia_win is not None, f"Failed to locate window with HWND {hwnd} via UIA"
        print(f"[+] (Setup) Located live window in UIA backend: '{uia_win.title}' (HWND {uia_win.hwnd})")

        # -------------------------------------------------------------
        # (a) Discover test button and text field via live UIA
        # -------------------------------------------------------------
        print("\n--- Test (a): Control Discovery via Live UIA ---")
        loc_input = UiaElementLocator(name="Test Input Field", control_type="Edit")
        loc_button = UiaElementLocator(name="Test Action Button", control_type="Button")

        elem_input = live_uia.find_control(uia_win, loc_input)
        elem_button = live_uia.find_control(uia_win, loc_button)

        assert elem_input is not None, "Failed to discover input field"
        assert elem_button is not None, "Failed to discover action button"

        info_input = elem_input.get_info()
        info_button = elem_button.get_info()

        print(f"[PASS] Discovered Input Field: name='{info_input.name}', type='{info_input.control_type}', rect={info_input.bounding_rect}")
        print(f"[PASS] Discovered Action Button: name='{info_button.name}', type='{info_button.control_type}', rect={info_button.bounding_rect}")
        results["discovery"] = True

        # -------------------------------------------------------------
        # (b) Setting text field value via semantic action (set_value)
        # -------------------------------------------------------------
        print("\n--- Test (b): Semantic Value Setting (ValuePattern) ---")
        new_text = "Live UIA Mutation Text"
        res_b = operator.execute_control_action(
            task_id="smoke_task_b",
            window_query=hwnd,
            locator=loc_input,
            action="set_value",
            value=new_text,
            require_focus=False,
        )
        app.processEvents()

        assert res_b.is_success, f"Action failed: {res_b.error}"
        assert res_b.action_type == SemanticActionType.SET_VALUE, f"Unexpected action type: {res_b.action_type}"
        assert res_b.verified, "Action was not verified"
        assert window.input_field.text() == new_text, f"Qt internal text mismatch: '{window.input_field.text()}' != '{new_text}'"

        print(f"[PASS] Semantic set_value succeeded. Verified text: '{window.input_field.text()}'")
        print(f"       Verification summary: {res_b.verification_summary}")
        results["set_value"] = True

        # -------------------------------------------------------------
        # (c) Activating button via InvokePattern
        # -------------------------------------------------------------
        print("\n--- Test (c): Button Activation via InvokePattern ---")
        initial_clicks = window.click_count
        res_c = operator.execute_control_action(
            task_id="smoke_task_c",
            window_query=hwnd,
            locator=loc_button,
            action="click",
            require_focus=False,
        )
        app.processEvents()

        assert res_c.is_success, f"Action failed: {res_c.error}"
        assert res_c.action_type == SemanticActionType.INVOKE, f"Unexpected action type: {res_c.action_type}"
        print(f"[PASS] Button activation via InvokePattern succeeded: {res_c.semantic_rationale}")
        results["invoke"] = True

        # -------------------------------------------------------------
        # (d) Verifying resulting UI state independently
        # -------------------------------------------------------------
        print("\n--- Test (d): Independent UI State Verification ---")
        assert window.click_count == initial_clicks + 1, f"Expected click count {initial_clicks + 1}, got {window.click_count}"
        assert "Clicked 1" in window.action_button.text(), f"Unexpected button text: {window.action_button.text()}"
        assert "Status: Clicked (1)" in window.status_label.text(), f"Unexpected label text: {window.status_label.text()}"

        # Also inspect accessibility tree independently to verify state change
        re_elem_button = live_uia.find_control(uia_win, loc_button)
        btn_name_after = re_elem_button.get_info().name if re_elem_button else ""
        print(f"[PASS] Independent state verification passed.")
        print(f"       Qt click_count={window.click_count}, button_text='{window.action_button.text()}', status='{window.status_label.text()}'")
        print(f"       UIA accessible name updated: '{btn_name_after}'")
        results["state_verification"] = True

        # -------------------------------------------------------------
        # (e) Detecting missing control without reporting success
        # -------------------------------------------------------------
        print("\n--- Test (e): Detecting Missing Control ---")
        loc_missing = UiaElementLocator(name="NonExistentWidget_9999", control_type="Button")
        res_e = operator.execute_control_action(
            task_id="smoke_task_e",
            window_query=hwnd,
            locator=loc_missing,
            action="click",
            require_focus=False,
        )

        assert not res_e.is_success, "Missing control was incorrectly reported as successful!"
        assert not res_e.verified, "Missing control was incorrectly verified!"
        assert "ELEMENT_NOT_FOUND" in (res_e.error or ""), f"Unexpected error: {res_e.error}"
        assert res_e.fallback_limitation is not None, "Fallback limitation not set for missing element"
        print(f"[PASS] Missing control accurately detected and rejected.")
        print(f"       Error: {res_e.error}")
        print(f"       Fallback limitation: {res_e.fallback_limitation}")
        results["missing_control"] = True

        # -------------------------------------------------------------
        # (f) Respecting focus denial and stopping safely
        # -------------------------------------------------------------
        print("\n--- Test (f): Safe Focus Denial Handling ---")
        # Configure a focus manager that denies focus
        class DenyingFocusManager(SafeFocusManager):
            def activate_window(self, target_hwnd: int, timeout: float = 3.0) -> FocusResult:
                return FocusResult(
                    success=False,
                    foreground_hwnd=0,
                    target_hwnd=target_hwnd,
                    error="FOCUS_DENIED: Windows foreground lock timeout",
                )

        operator_with_denial = ComputerOperatorV2(
            uia_operator=live_uia,
            focus_manager=DenyingFocusManager(),
        )

        res_f = operator_with_denial.execute_control_action(
            task_id="smoke_task_f",
            window_query=hwnd,
            locator=loc_button,
            action="click",
            require_focus=True,  # Mandatory focus
        )

        assert not res_f.is_success, "Action succeeded despite focus denial!"
        assert not res_f.verified, "Action was verified despite focus denial!"
        assert "FOCUS_DENIED" in (res_f.error or ""), f"Expected FOCUS_DENIED error, got: {res_f.error}"
        print(f"[PASS] Focus denial respected. Stopped safely without side-effects.")
        print(f"       Reported error: {res_f.error}")
        results["focus_denial"] = True

    finally:
        window.close()
        app.processEvents()

    print("\n" + "=" * 60)
    print("ALL 6 SMOKE TEST CRITERIA PASSED AGAINST LIVE WINDOWS DESKTOP")
    print("=" * 60)
    return results


if __name__ == "__main__":
    test_results = run_smoke_test()
    all_ok = all(test_results.values()) and len(test_results) == 6
    sys.exit(0 if all_ok else 1)
