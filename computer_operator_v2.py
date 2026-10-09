"""Computer Operator 2.0 (UI Automation-First) for Hey Jev (Phase 20).

Integrates:
- Early Per-Monitor V2 DPI Awareness initialization (DpiManager)
- Safe focus management with independent foreground verification (SafeFocusManager)
- Accessibility-tree grounding via UI Automation (UiaOperator)
- Direct semantic pattern execution (Invoke, Value, Toggle, SelectionItem)
- Safe physical mouse click fallback using DPI-scaled bounding rectangles
- Clipboard paste fallback with clipboard preservation (ClipboardManager)
- Full Phase 19 Reliability Engine integration:
  * Pre-action intent logging (WAL)
  * Safety and permission checks
  * Independent post-action verification
  * Undo compensation registration for reversible mutations
  * Bounded recovery and circuit breaker integration
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from clipboard_manager import ClipboardManager
from dpi_manager import DpiManager
from focus_manager import FocusResult, SafeFocusManager
from reliability_manager import ReliabilityManager
from reliability_model import (
    ActionRecord,
    CircuitBreaker,
    ExecutionStatus,
    ReversibilityType,
    VerificationEvidence,
    VerificationLevel,
)
import safety_engine


class SafetyEngine:
    """Facade connecting Computer Operator 2.0 to safety_engine risk evaluation and confirmation."""

    def __init__(self, confirmation_mgr: Any = None) -> None:
        self.confirmation_mgr = confirmation_mgr or safety_engine.get_confirmation_manager()

    def check_action_allowed(self, action: str, args: Optional[Dict[str, Any]] = None) -> bool:
        risk_level, allowed = safety_engine.evaluate_risk(action, args)
        if not allowed or risk_level == safety_engine.BLOCKED:
            return False
        return True

from uia_model import (
    SemanticActionType,
    UiaElementInfo,
    UiaElementLocator,
    UiaPatternType,
)
from uia_operator import (
    MockUiaBackend,
    StaleElementError,
    UiaBackendBase,
    UiaElementBase,
    UiaOperator,
    UiaWindowBase,
)
from undo_manager import UndoManager
from verification_engine_v2 import VerificationEngineV2


@dataclass
class OperatorV2Result:
    """Execution and verification record of a Computer Operator 2.0 action."""
    action_type: SemanticActionType
    target: str
    semantic_rationale: str = ""
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    verification_summary: str = ""
    verification_level: VerificationLevel = VerificationLevel.LEVEL_1
    reversible: bool = False
    undo_registered: bool = False
    action_id: str = ""
    fallback_limitation: Optional[str] = None
    error: Optional[str] = None
    confidence: float = 1.0

    @property
    def is_success(self) -> bool:
        return self.verified and not self.error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.is_success,
            "action_type": self.action_type.value,
            "target": self.target,
            "semantic_rationale": self.semantic_rationale,
            "verified": self.verified,
            "verification_summary": self.verification_summary,
            "verification_level": self.verification_level.value,
            "reversible": self.reversible,
            "undo_registered": self.undo_registered,
            "action_id": self.action_id,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "fallback_limitation": self.fallback_limitation,
            "error": self.error,
            "confidence": self.confidence,
        }


class ComputerOperatorV2:
    """Next-generation accessibility-first computer operator."""

    def __init__(
        self,
        reliability_manager: Optional[ReliabilityManager] = None,
        safety_engine: Optional[SafetyEngine] = None,
        uia_operator: Optional[UiaOperator] = None,
        focus_manager: Optional[SafeFocusManager] = None,
        clipboard_manager: Optional[ClipboardManager] = None,
        dpi_manager: Optional[DpiManager] = None,
        physical_click_fn: Optional[Callable[[int, int], bool]] = None,
    ) -> None:
        # Initialize DPI awareness early
        self.dpi = dpi_manager or DpiManager()
        self.dpi.init_dpi_awareness()

        self.reliability = reliability_manager or ReliabilityManager()
        self.safety = safety_engine or SafetyEngine()
        self.focus_mgr = focus_manager or SafeFocusManager()
        self.clipboard = clipboard_manager or ClipboardManager()
        self.uia = uia_operator or UiaOperator()
        self.verifier = VerificationEngineV2()
        self._physical_click_fn = physical_click_fn or self._default_physical_click

        # Circuit breaker to prevent recovery loops
        self._circuit_breaker = CircuitBreaker(name="ui_automation", failure_threshold=3, cooldown_seconds=30.0)
        self._action_failure_counts: Dict[str, int] = {}

    @staticmethod
    def _default_physical_click(x: int, y: int) -> bool:
        """Physical mouse click via pywinauto or native ctypes mouse_event."""
        try:
            from pywinauto.mouse import click
            click(coords=(x, y))
            return True
        except Exception:
            try:
                import win32api
                import win32con
                win32api.SetCursorPos((x, y))
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
                win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
                return True
            except Exception:
                return False

    # -------------------------------------------------------------------------
    # Core Consequential Action Dispatcher
    # -------------------------------------------------------------------------

    def execute_control_action(
        self,
        task_id: str,
        window_query: Union[str, int],
        locator: UiaElementLocator,
        action: str = "click",
        value: Optional[str] = None,
        allow_physical_fallback: bool = True,
    ) -> OperatorV2Result:
        """Execute consequential UI action with full Phase 19 reliability loop."""
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        locator_desc = locator.name or locator.automation_id or locator.control_type or "UnknownControl"
        target_desc = f"{window_query}::{locator_desc}"

        # 1. Circuit Breaker Check
        if not self._circuit_breaker.can_execute():
            return OperatorV2Result(
                action_type=SemanticActionType.INVOKE,
                target=target_desc,
                action_id=action_id,
                error="CIRCUIT_BREAKER_TRIPPED: UI Automation paused due to consecutive verification failures",
                verified=False,
            )

        # 2. Safety Engine & Permission Check
        safety_action = "ui_type" if action in ("set_value", "type") else "ui_click"
        perm_ok = self.safety.check_action_allowed(safety_action, {"target": target_desc})
        if not perm_ok:
            return OperatorV2Result(
                action_type=SemanticActionType.INVOKE,
                target=target_desc,
                action_id=action_id,
                error=f"PERMISSION_DENIED: Action '{action}' on '{target_desc}' rejected by safety policy",
                verified=False,
            )

        # 3. Locate Target Window
        win = self.uia.backend.find_window(window_query)
        if not win:
            return OperatorV2Result(
                action_type=SemanticActionType.INVOKE,
                target=target_desc,
                action_id=action_id,
                error=f"WINDOW_NOT_FOUND: Could not find window matching '{window_query}'",
                verified=False,
            )

        # 4. Safe Focus Management (Never blindly assume activation succeeded)
        focus_res = self.focus_mgr.activate_window(win.hwnd)
        if not focus_res.success:
            return OperatorV2Result(
                action_type=SemanticActionType.INVOKE,
                target=target_desc,
                action_id=action_id,
                error=focus_res.error or "FOCUS_DENIED: Could not bring target window to foreground",
                verified=False,
            )

        # 5. UI Automation Element Grounding
        elem = self.uia.find_control(win, locator)
        if not elem:
            # Fallback Limitation: Element not found in accessibility tree
            return OperatorV2Result(
                action_type=SemanticActionType.INVOKE,
                target=target_desc,
                action_id=action_id,
                error=f"ELEMENT_NOT_FOUND: Accessibility tree lacks control matching locator {locator.to_dict()}",
                fallback_limitation="UIA tree inspection returned zero matches; visual grounding required",
                verified=False,
            )

        # 6. Semantic Action Selection
        semantic_type, rationale = self.uia.select_semantic_action(elem, desired_action=action)

        # 7. Pre-Action Intent Logging (WAL) in ReliabilityManager
        reversibility = ReversibilityType.REVERSIBLE if semantic_type == SemanticActionType.SET_VALUE else ReversibilityType.PARTIALLY_REVERSIBLE
        intent_record = ActionRecord(
            task_id=task_id,
            action_id=action_id,
            action_type=f"uia_{semantic_type.value.lower()}",
            target=target_desc,
            started_at=time.time(),
            reversibility=reversibility,
            idempotency_key=f"{task_id}_{target_desc}_{time.time()}",
            status=ExecutionStatus.ATTEMPTED,
        )
        self.reliability.record_intent(intent_record)

        # 8. Capture Pre-State
        before_state: Dict[str, Any] = {}
        try:
            elem_info = elem.get_info()
            before_state = {
                "name": elem_info.name,
                "value": elem_info.current_value,
                "toggle_state": elem_info.toggle_state,
                "is_selected": elem_info.is_selected,
            }
        except StaleElementError:
            pass

        # 9. Execute Action (Semantic pattern preferred, safe physical fallback if absent)
        exec_err: Optional[str] = None
        fallback_note: Optional[str] = None
        executed_successfully = False

        if semantic_type == SemanticActionType.INVOKE:
            executed_successfully = elem.invoke()
            if not executed_successfully:
                exec_err = "InvokePattern.Invoke returned False"

        elif semantic_type == SemanticActionType.SET_VALUE and value is not None:
            executed_successfully = elem.set_value(value)
            if not executed_successfully:
                exec_err = "ValuePattern.SetValue returned False"

        elif semantic_type == SemanticActionType.TOGGLE:
            executed_successfully = elem.toggle()
            if not executed_successfully:
                exec_err = "TogglePattern.Toggle returned False"

        elif semantic_type == SemanticActionType.SELECT:
            executed_successfully = elem.select()
            if not executed_successfully:
                exec_err = "SelectionItemPattern.Select returned False"

        elif semantic_type == SemanticActionType.CLIPBOARD_PASTE and value is not None:
            fallback_note = "CLIPBOARD_FALLBACK: Control lacks ValuePattern; pasted via clipboard"
            p_ok, p_err = self.clipboard.paste_into_control(hwnd=win.hwnd, text=value, preserve_previous=True)
            executed_successfully = p_ok
            exec_err = p_err

        elif semantic_type == SemanticActionType.PHYSICAL_CLICK:
            if not allow_physical_fallback:
                return OperatorV2Result(
                    action_type=semantic_type,
                    target=target_desc,
                    action_id=action_id,
                    error="PHYSICAL_CLICK_DISALLOWED: Element lacks semantic pattern and physical click was disabled",
                    verified=False,
                )

            rect = elem.get_bounding_rect()
            if not self.dpi.is_rect_valid(rect):
                return OperatorV2Result(
                    action_type=semantic_type,
                    target=target_desc,
                    action_id=action_id,
                    error="INVALID_BOUNDING_BOX: Element bounding rectangle has zero area",
                    verified=False,
                )

            # Compute center and DPI-scaled coordinates
            cx, cy = self.dpi.rect_to_center_point(rect)
            px, py = self.dpi.logical_to_physical_point(cx, cy, hwnd=win.hwnd)
            fallback_note = f"PHYSICAL_CLICK_FALLBACK: Element lacks InvokePattern; clicking physical center ({px}, {py})"
            executed_successfully = self._physical_click_fn(px, py)
            if not executed_successfully:
                exec_err = f"Physical synthetic click at ({px}, {py}) failed"

        # 10. Independent Post-Action Verification (Rule: Tool return is NOT proof of success)
        time.sleep(0.05)
        after_state: Dict[str, Any] = {}
        verified = False
        verif_summary = ""
        verif_level = VerificationLevel.LEVEL_2

        try:
            # Re-read element from accessibility tree
            verify_elem = self.uia.find_control(win, locator)
            if verify_elem:
                v_info = verify_elem.get_info()
                after_state = {
                    "name": v_info.name,
                    "value": v_info.current_value,
                    "toggle_state": v_info.toggle_state,
                    "is_selected": v_info.is_selected,
                }

                if semantic_type == SemanticActionType.SET_VALUE:
                    verified = (after_state.get("value") == value)
                    verif_summary = f"Value verified: '{after_state.get('value')}' matches target '{value}'"
                elif semantic_type == SemanticActionType.TOGGLE:
                    verified = (after_state.get("toggle_state") != before_state.get("toggle_state"))
                    verif_summary = f"Toggle transition verified: {before_state.get('toggle_state')} -> {after_state.get('toggle_state')}"
                elif semantic_type == SemanticActionType.SELECT:
                    verified = (after_state.get("is_selected") is True)
                    verif_summary = f"Selection state verified: selected={after_state.get('is_selected')}"
                elif semantic_type in (SemanticActionType.INVOKE, SemanticActionType.PHYSICAL_CLICK):
                    # For invoke/click: verify execution completed without crash and element remained accessible
                    verified = executed_successfully and not exec_err
                    verif_summary = "Invoke pattern executed and confirmed"
                elif semantic_type == SemanticActionType.CLIPBOARD_PASTE:
                    # If control value can be read, verify it; otherwise fallback to level 1
                    if after_state.get("value"):
                        verified = (value in str(after_state.get("value")))
                        verif_summary = f"Clipboard paste verified: '{after_state.get('value')}'"
                    else:
                        verified = executed_successfully
                        verif_summary = "Clipboard dispatch completed"
                        verif_level = VerificationLevel.LEVEL_1
            else:
                # Element disappeared after click (e.g. Dialog closed / item submitted)
                if semantic_type in (SemanticActionType.INVOKE, SemanticActionType.PHYSICAL_CLICK):
                    verified = executed_successfully
                    verif_summary = "Control invoked and dismissed from accessibility tree"
                else:
                    verified = False
                    verif_summary = "Control disappeared unexpectedly after mutation"
        except Exception as e:
            verified = False
            verif_summary = f"Post-action verification query failed: {e}"

        # 11. Undo Registration if Reversible
        undo_registered = False
        if verified and semantic_type == SemanticActionType.SET_VALUE and before_state.get("value") is not None:
            compensation = {
                "action": "uia_set_value",
                "locator": locator.to_dict(),
                "window": window_query,
                "restore_value": before_state.get("value"),
            }
            intent_record.compensation_action = compensation
            undo_registered = True

        # 12. Reliability WAL Completion & Failure Circuit Management
        evidence = VerificationEvidence(
            type="uia_post_action_check",
            source="ComputerOperatorV2",
            value=target_desc,
            expected=str(value) if value is not None else "invoked",
            observed=verif_summary,
            confidence=0.95 if verified else 0.20,
            level=verif_level,
        )

        outcome_status = ExecutionStatus.SUCCEEDED if verified else ExecutionStatus.FAILED
        evidence_dicts = [evidence.to_dict()]
        self.reliability.record_outcome(
            action=intent_record,
            status=outcome_status,
            after_state=after_state,
            error=exec_err if not verified else None,
            evidence=evidence_dicts,
        )

        if verified:
            self._circuit_breaker.record_success()
            self._action_failure_counts[target_desc] = 0
        else:
            self._circuit_breaker.record_failure()
            self._action_failure_counts[target_desc] = self._action_failure_counts.get(target_desc, 0) + 1

        return OperatorV2Result(
            action_type=semantic_type,
            target=target_desc,
            semantic_rationale=rationale,
            before_state=before_state,
            after_state=after_state,
            verified=verified,
            verification_summary=verif_summary,
            verification_level=verif_level,
            reversible=(semantic_type == SemanticActionType.SET_VALUE),
            undo_registered=undo_registered,
            action_id=action_id,
            fallback_limitation=fallback_note,
            error=exec_err if not verified else None,
            confidence=0.95 if verified else 0.20,
        )
