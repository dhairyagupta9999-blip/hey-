"""UI Automation Operator and Backends for Hey Jev (Phase 20).

Provides:
- Abstract UIA element and backend interfaces
- PywinautoUiaBackend: Live Windows UIA adapter mapping COM patterns
- MockUiaBackend: Isolated deterministic in-memory provider for regression tests
- UiaOperator: Locating controls, resolving ambiguity, selecting optimal semantic actions,
  handling stale element references, and physical click fallbacks.
"""
from __future__ import annotations

import abc
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from dpi_manager import DpiManager
from uia_model import (
    SemanticActionType,
    UiaControlType,
    UiaElementInfo,
    UiaElementLocator,
    UiaPatternType,
)


class StaleElementError(Exception):
    """Raised when an inspected element reference is no longer valid in the accessibility tree."""
    pass


# =============================================================================
# Abstract Interfaces
# =============================================================================

class UiaElementBase(abc.ABC):
    """Abstract interface for accessible UI elements."""

    @abc.abstractmethod
    def get_info(self) -> UiaElementInfo:
        pass

    @abc.abstractmethod
    def get_children(self) -> List[UiaElementBase]:
        pass

    @abc.abstractmethod
    def has_pattern(self, pattern: str) -> bool:
        pass

    @abc.abstractmethod
    def invoke(self) -> bool:
        pass

    @abc.abstractmethod
    def set_value(self, value: str) -> bool:
        pass

    @abc.abstractmethod
    def get_value(self) -> Optional[str]:
        pass

    @abc.abstractmethod
    def toggle(self) -> bool:
        pass

    @abc.abstractmethod
    def get_toggle_state(self) -> Optional[int]:
        pass

    @abc.abstractmethod
    def select(self) -> bool:
        pass

    @abc.abstractmethod
    def is_selected(self) -> Optional[bool]:
        pass

    @abc.abstractmethod
    def expand(self) -> bool:
        pass

    @abc.abstractmethod
    def collapse(self) -> bool:
        pass

    @abc.abstractmethod
    def get_bounding_rect(self) -> Dict[str, int]:
        pass

    @abc.abstractmethod
    def is_stale(self) -> bool:
        pass


class UiaWindowBase(abc.ABC):
    """Abstract interface for desktop windows containing UIA element trees."""

    def __init__(self, hwnd: int, title: str) -> None:
        self.hwnd = hwnd
        self.title = title

    @abc.abstractmethod
    def get_root_element(self) -> UiaElementBase:
        pass

    def find_elements(
        self,
        locator: UiaElementLocator,
        parent: Optional[UiaElementBase] = None,
        max_depth: int = 10,
    ) -> List[UiaElementBase]:
        """Search recursively for all elements matching the locator."""
        root = parent or self.get_root_element()
        results: List[UiaElementBase] = []

        # If parent_locator is specified, resolve parent first
        search_roots = [root]
        if locator.parent_locator:
            parents = self.find_elements(locator.parent_locator, parent=root, max_depth=max_depth)
            if not parents:
                return []
            idx = locator.parent_locator.index
            search_roots = [parents[idx]] if idx < len(parents) else [parents[0]]

        def traverse(elem: UiaElementBase, current_depth: int) -> None:
            if current_depth > max_depth or elem.is_stale():
                return
            try:
                info = elem.get_info()
                if locator.matches(info):
                    results.append(elem)
                for child in elem.get_children():
                    traverse(child, current_depth + 1)
            except StaleElementError:
                pass

        for s_root in search_roots:
            for child in s_root.get_children():
                traverse(child, 1)

        return results

    def find_element(
        self,
        locator: UiaElementLocator,
        parent: Optional[UiaElementBase] = None,
    ) -> Optional[UiaElementBase]:
        """Find single element by locator, using index to disambiguate multiple matches."""
        elements = self.find_elements(locator, parent=parent)
        if not elements:
            return None
        idx = locator.index
        if idx < len(elements):
            return elements[idx]
        return elements[0]


class UiaBackendBase(abc.ABC):
    """Abstract provider for UI Automation discovery."""

    @abc.abstractmethod
    def find_window(self, query_or_hwnd: Union[str, int]) -> Optional[UiaWindowBase]:
        pass

    @abc.abstractmethod
    def list_windows(self) -> List[UiaWindowBase]:
        pass


# =============================================================================
# Mock UIA Implementation (Deterministic Isolation for Tests)
# =============================================================================

class MockUiaElement(UiaElementBase):
    """In-memory UIA element for isolated, deterministic unit testing."""

    def __init__(
        self,
        automation_id: str = "",
        name: str = "",
        control_type: str = "Button",
        class_name: str = "",
        bounding_rect: Optional[Dict[str, int]] = None,
        supported_patterns: Optional[List[str]] = None,
        value: Optional[str] = None,
        toggle_state: Optional[int] = 0,
        is_selected: Optional[bool] = False,
        children: Optional[List[MockUiaElement]] = None,
    ) -> None:
        self.automation_id = automation_id
        self.name = name
        self.control_type = control_type
        self.class_name = class_name
        self.bounding_rect = bounding_rect or {"left": 10, "top": 10, "right": 110, "bottom": 40}
        self.supported_patterns = set(supported_patterns or [])
        self._value = value
        self._toggle_state = toggle_state
        self._is_selected = is_selected
        self._children: List[MockUiaElement] = children or []
        self._is_stale = False
        self._is_expanded = False
        self.invoked = False

    def make_stale(self) -> None:
        """Simulate element detachment / destruction."""
        self._is_stale = True

    def is_stale(self) -> bool:
        return self._is_stale

    def _check_stale(self) -> None:
        if self._is_stale:
            raise StaleElementError(f"Element '{self.name}' ({self.automation_id}) is stale.")

    def get_info(self) -> UiaElementInfo:
        self._check_stale()
        return UiaElementInfo(
            automation_id=self.automation_id,
            name=self.name,
            control_type=self.control_type,
            class_name=self.class_name,
            bounding_rect=dict(self.bounding_rect),
            is_enabled=True,
            is_visible=True,
            supported_patterns=list(self.supported_patterns),
            current_value=self._value,
            toggle_state=self._toggle_state,
            is_selected=self._is_selected,
        )

    @property
    def children(self) -> List[MockUiaElement]:
        return self._children

    @children.setter
    def children(self, val: List[MockUiaElement]) -> None:
        self._children = val

    def get_children(self) -> List[UiaElementBase]:
        self._check_stale()
        return list(self._children)

    def add_child(self, child: MockUiaElement) -> None:
        self._children.append(child)

    def has_pattern(self, pattern: str) -> bool:
        self._check_stale()
        return pattern in self.supported_patterns

    def invoke(self) -> bool:
        self._check_stale()
        if UiaPatternType.INVOKE.value not in self.supported_patterns:
            return False
        self.invoked = True
        return True

    def set_value(self, value: str) -> bool:
        self._check_stale()
        if UiaPatternType.VALUE.value not in self.supported_patterns:
            return False
        self._value = value
        return True

    def get_value(self) -> Optional[str]:
        self._check_stale()
        return self._value

    def toggle(self) -> bool:
        self._check_stale()
        if UiaPatternType.TOGGLE.value not in self.supported_patterns:
            return False
        self._toggle_state = 1 if self._toggle_state == 0 else 0
        return True

    def get_toggle_state(self) -> Optional[int]:
        self._check_stale()
        return self._toggle_state

    def select(self) -> bool:
        self._check_stale()
        if UiaPatternType.SELECTION_ITEM.value not in self.supported_patterns:
            return False
        self._is_selected = True
        return True

    def is_selected(self) -> Optional[bool]:
        self._check_stale()
        return self._is_selected

    def expand(self) -> bool:
        self._check_stale()
        if UiaPatternType.EXPAND_COLLAPSE.value not in self.supported_patterns:
            return False
        self._is_expanded = True
        return True

    def collapse(self) -> bool:
        self._check_stale()
        if UiaPatternType.EXPAND_COLLAPSE.value not in self.supported_patterns:
            return False
        self._is_expanded = False
        return True

    def get_bounding_rect(self) -> Dict[str, int]:
        self._check_stale()
        return dict(self.bounding_rect)


class MockUiaWindow(UiaWindowBase):
    """In-memory mock window containing mock UIA tree."""

    def __init__(self, hwnd: int, title: str, root_element: Optional[MockUiaElement] = None) -> None:
        super().__init__(hwnd=hwnd, title=title)
        self.root_element = root_element or MockUiaElement(name=title, control_type="Window")

    def get_root_element(self) -> UiaElementBase:
        return self.root_element


class MockUiaBackend(UiaBackendBase):
    """In-memory mock UIA provider for unit testing."""

    def __init__(self) -> None:
        self.windows: Dict[int, MockUiaWindow] = {}

    def register_window(self, window: MockUiaWindow) -> None:
        self.windows[window.hwnd] = window

    def find_window(self, query_or_hwnd: Union[str, int]) -> Optional[UiaWindowBase]:
        if isinstance(query_or_hwnd, int):
            return self.windows.get(query_or_hwnd)
        q = str(query_or_hwnd).lower().strip()
        for w in self.windows.values():
            if q in w.title.lower():
                return w
        return None

    def list_windows(self) -> List[UiaWindowBase]:
        return list(self.windows.values())


# =============================================================================
# Pywinauto Live UIA Implementation
# =============================================================================

class PywinautoUiaElement(UiaElementBase):
    """Live Windows UIA element wrapping a pywinauto UIAWrapper object."""

    def __init__(self, wrapper: Any) -> None:
        self._wrapper = wrapper

    def is_stale(self) -> bool:
        try:
            _ = self._wrapper.element_info.name
            return False
        except Exception:
            return True

    def _check_stale(self) -> None:
        if self.is_stale():
            raise StaleElementError("Underlying UIA control is detached or destroyed")

    def get_info(self) -> UiaElementInfo:
        self._check_stale()
        info = self._wrapper.element_info
        patterns: List[str] = []
        try:
            for p in ["Invoke", "Value", "Toggle", "SelectionItem", "ExpandCollapse", "Text"]:
                if hasattr(self._wrapper, f"iface_{p.lower()}"):
                    patterns.append(p)
        except Exception:
            pass

        rect = {"left": 0, "top": 0, "right": 0, "bottom": 0}
        try:
            r = info.rectangle
            rect = {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
        except Exception:
            pass

        val = None
        try:
            if hasattr(self._wrapper, "iface_value"):
                val = self._wrapper.iface_value.CurrentValue
        except Exception:
            pass

        toggle_st = None
        try:
            if hasattr(self._wrapper, "iface_toggle"):
                toggle_st = self._wrapper.iface_toggle.CurrentToggleState
        except Exception:
            pass

        is_sel = None
        try:
            if hasattr(self._wrapper, "iface_selection_item"):
                is_sel = bool(self._wrapper.iface_selection_item.CurrentIsSelected)
        except Exception:
            pass

        return UiaElementInfo(
            automation_id=info.automation_id or "",
            name=info.name or "",
            control_type=info.control_type or "",
            class_name=info.class_name or "",
            bounding_rect=rect,
            is_enabled=bool(info.enabled) if hasattr(info, "enabled") else True,
            is_visible=bool(info.visible) if hasattr(info, "visible") else True,
            supported_patterns=patterns,
            current_value=val,
            toggle_state=toggle_st,
            is_selected=is_sel,
        )

    def get_children(self) -> List[UiaElementBase]:
        self._check_stale()
        try:
            return [PywinautoUiaElement(c) for c in self._wrapper.children()]
        except Exception:
            return []

    def has_pattern(self, pattern: str) -> bool:
        self._check_stale()
        return hasattr(self._wrapper, f"iface_{pattern.lower()}")

    def invoke(self) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "invoke"):
                self._wrapper.invoke()
                return True
            if hasattr(self._wrapper, "iface_invoke"):
                self._wrapper.iface_invoke.Invoke()
                return True
        except Exception:
            pass
        return False

    def set_value(self, value: str) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "set_edit_text"):
                self._wrapper.set_edit_text(value)
                return True
            if hasattr(self._wrapper, "iface_value"):
                self._wrapper.iface_value.SetValue(value)
                return True
        except Exception:
            pass
        return False

    def get_value(self) -> Optional[str]:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "iface_value"):
                return self._wrapper.iface_value.CurrentValue
            if hasattr(self._wrapper, "window_text"):
                return self._wrapper.window_text()
        except Exception:
            pass
        return None

    def toggle(self) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "toggle"):
                self._wrapper.toggle()
                return True
            if hasattr(self._wrapper, "iface_toggle"):
                self._wrapper.iface_toggle.Toggle()
                return True
        except Exception:
            pass
        return False

    def get_toggle_state(self) -> Optional[int]:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "iface_toggle"):
                return self._wrapper.iface_toggle.CurrentToggleState
        except Exception:
            pass
        return None

    def select(self) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "select"):
                self._wrapper.select()
                return True
            if hasattr(self._wrapper, "iface_selection_item"):
                self._wrapper.iface_selection_item.Select()
                return True
        except Exception:
            pass
        return False

    def is_selected(self) -> Optional[bool]:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "iface_selection_item"):
                return bool(self._wrapper.iface_selection_item.CurrentIsSelected)
        except Exception:
            pass
        return None

    def expand(self) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "expand"):
                self._wrapper.expand()
                return True
            if hasattr(self._wrapper, "iface_expand_collapse"):
                self._wrapper.iface_expand_collapse.Expand()
                return True
        except Exception:
            pass
        return False

    def collapse(self) -> bool:
        self._check_stale()
        try:
            if hasattr(self._wrapper, "collapse"):
                self._wrapper.collapse()
                return True
            if hasattr(self._wrapper, "iface_expand_collapse"):
                self._wrapper.iface_expand_collapse.Collapse()
                return True
        except Exception:
            pass
        return False

    def get_bounding_rect(self) -> Dict[str, int]:
        self._check_stale()
        try:
            r = self._wrapper.element_info.rectangle
            return {"left": r.left, "top": r.top, "right": r.right, "bottom": r.bottom}
        except Exception:
            return {"left": 0, "top": 0, "right": 0, "bottom": 0}


class PywinautoUiaWindow(UiaWindowBase):
    """Live Windows UIA window wrapping a connected pywinauto WindowSpecification."""

    def __init__(self, hwnd: int, title: str, app_obj: Any) -> None:
        super().__init__(hwnd=hwnd, title=title)
        self._app = app_obj

    def get_root_element(self) -> UiaElementBase:
        root_wrap = self._app.window(handle=self.hwnd).wrapper_object()
        return PywinautoUiaElement(root_wrap)


class PywinautoUiaBackend(UiaBackendBase):
    """Live Windows UIA backend utilizing pywinauto Desktop(backend='uia')."""

    def find_window(self, query_or_hwnd: Union[str, int]) -> Optional[UiaWindowBase]:
        from pywinauto import Application, Desktop
        import win32gui

        target_hwnd = None
        target_title = ""
        if isinstance(query_or_hwnd, int):
            if win32gui.IsWindow(query_or_hwnd):
                target_hwnd = query_or_hwnd
                target_title = win32gui.GetWindowText(target_hwnd)
        else:
            q = str(query_or_hwnd).lower().strip()
            def enum_cb(h, _):
                nonlocal target_hwnd, target_title
                if win32gui.IsWindowVisible(h) and not win32gui.GetParent(h):
                    t = win32gui.GetWindowText(h)
                    if t and q in t.lower():
                        target_hwnd = h
                        target_title = t
                        return False
                return True
            try:
                win32gui.EnumWindows(enum_cb, None)
            except Exception:
                pass

        if not target_hwnd:
            return None

        try:
            app = Application(backend="uia").connect(handle=target_hwnd, timeout=2.0)
            return PywinautoUiaWindow(hwnd=target_hwnd, title=target_title, app_obj=app)
        except Exception:
            return None

    def list_windows(self) -> List[UiaWindowBase]:
        import win32gui
        results: List[UiaWindowBase] = []
        def enum_cb(h, _):
            if win32gui.IsWindowVisible(h) and not win32gui.GetParent(h):
                t = win32gui.GetWindowText(h)
                if t.strip():
                    results.append(PywinautoUiaWindow(hwnd=h, title=t, app_obj=None))
            return True
        try:
            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            pass
        return results


# =============================================================================
# UiaOperator Coordinator
# =============================================================================

class UiaOperator:
    """High-level semantic control operator prioritizing UIA patterns over physical clicks."""

    def __init__(self, backend: Optional[UiaBackendBase] = None) -> None:
        self.backend = backend or PywinautoUiaBackend()

    def select_semantic_action(
        self,
        element: UiaElementBase,
        desired_action: str = "click",
    ) -> Tuple[SemanticActionType, str]:
        """Select the most specific semantic pattern supported by the element.

        Returns (action_type, rationale).
        """
        desired = desired_action.lower().strip()

        if desired in ("set_value", "type", "input", "write"):
            if element.has_pattern(UiaPatternType.VALUE.value):
                return SemanticActionType.SET_VALUE, "Element supports ValuePattern.SetValue"
            return SemanticActionType.CLIPBOARD_PASTE, "Element lacks ValuePattern; fallback to clipboard paste"

        if desired in ("toggle", "check", "uncheck"):
            if element.has_pattern(UiaPatternType.TOGGLE.value):
                return SemanticActionType.TOGGLE, "Element supports TogglePattern.Toggle"

        if desired in ("select", "choose"):
            if element.has_pattern(UiaPatternType.SELECTION_ITEM.value):
                return SemanticActionType.SELECT, "Element supports SelectionItemPattern.Select"

        # General click / invocation
        if element.has_pattern(UiaPatternType.INVOKE.value):
            return SemanticActionType.INVOKE, "Element supports InvokePattern.Invoke (semantic)"

        if element.has_pattern(UiaPatternType.TOGGLE.value):
            return SemanticActionType.TOGGLE, "Element is Toggleable (CheckBox/Switch)"

        if element.has_pattern(UiaPatternType.SELECTION_ITEM.value):
            return SemanticActionType.SELECT, "Element is Selectable (ListItem/RadioButton)"

        # Fallback to physical click
        return SemanticActionType.PHYSICAL_CLICK, "Element lacks semantic patterns; fallback to bounding-box click"

    def find_control(
        self,
        window: UiaWindowBase,
        locator: UiaElementLocator,
        max_retries: int = 2,
    ) -> Optional[UiaElementBase]:
        """Find a control in the window, retrying if transient stale reference occurs."""
        for attempt in range(max_retries + 1):
            try:
                elem = window.find_element(locator)
                if elem and not elem.is_stale():
                    return elem
            except StaleElementError:
                if attempt == max_retries:
                    return None
                time.sleep(0.05)
            except Exception:
                pass
        return None
