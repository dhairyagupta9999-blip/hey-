"""UI Automation Models and Locators for Hey Jev (Phase 20).

Defines:
- Standard UIA Control Types and Supported Patterns
- UiaElementLocator for deterministic multi-attribute element addressing
- UiaElementInfo for serializable element state snapshots
- SemanticActionType prioritizing pattern invocations over physical mouse clicks
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class UiaControlType(str, Enum):
    """Standard Windows UI Automation control types."""
    BUTTON = "Button"
    EDIT = "Edit"
    CHECKBOX = "CheckBox"
    RADIOBUTTON = "RadioButton"
    COMBOBOX = "ComboBox"
    LIST = "List"
    LISTITEM = "ListItem"
    MENU = "Menu"
    MENUITEM = "MenuItem"
    TAB = "Tab"
    TABITEM = "TabItem"
    WINDOW = "Window"
    PANE = "Pane"
    GROUP = "Group"
    TEXT = "Text"
    HYPERLINK = "Hyperlink"
    CUSTOM = "Custom"
    DOCUMENT = "Document"
    SCROLLBAR = "ScrollBar"
    STATUSBAR = "StatusBar"
    TOOLBAR = "ToolBar"
    TREE = "Tree"
    TREEITEM = "TreeItem"


class UiaPatternType(str, Enum):
    """Standard UI Automation Control Patterns."""
    INVOKE = "Invoke"
    VALUE = "Value"
    TOGGLE = "Toggle"
    SELECTION_ITEM = "SelectionItem"
    SELECTION = "Selection"
    EXPAND_COLLAPSE = "ExpandCollapse"
    TEXT = "Text"
    SCROLL = "Scroll"
    WINDOW = "Window"
    TRANSFORM = "Transform"


class SemanticActionType(str, Enum):
    """Semantic action chosen for UI interaction."""
    INVOKE = "INVOKE"                    # InvokePattern (buttons, links, menuitems)
    SET_VALUE = "SET_VALUE"              # ValuePattern (textboxes, edits)
    TOGGLE = "TOGGLE"                    # TogglePattern (checkboxes)
    SELECT = "SELECT"                    # SelectionItemPattern (radiobuttons, listitems)
    EXPAND = "EXPAND"                    # ExpandCollapsePattern
    COLLAPSE = "COLLAPSE"                # ExpandCollapsePattern
    PHYSICAL_CLICK = "PHYSICAL_CLICK"    # Bounding box physical mouse click fallback
    CLIPBOARD_PASTE = "CLIPBOARD_PASTE"  # Keyboard clipboard paste fallback


@dataclass
class UiaElementLocator:
    """Multi-attribute locator for deterministic control discovery and disambiguation."""
    automation_id: Optional[str] = None
    name: Optional[str] = None
    control_type: Optional[str] = None
    class_name: Optional[str] = None
    partial_name: Optional[str] = None
    parent_locator: Optional[UiaElementLocator] = None
    index: int = 0

    def matches(self, elem_info: UiaElementInfo) -> bool:
        """Check whether a given UiaElementInfo satisfies all specified criteria."""
        if self.automation_id and elem_info.automation_id != self.automation_id:
            return False
        if self.control_type and elem_info.control_type.lower() != self.control_type.lower():
            return False
        if self.class_name and elem_info.class_name.lower() != self.class_name.lower():
            return False
        if self.name and elem_info.name.lower() != self.name.lower():
            return False
        if self.partial_name and self.partial_name.lower() not in elem_info.name.lower():
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "automation_id": self.automation_id,
            "name": self.name,
            "control_type": self.control_type,
            "class_name": self.class_name,
            "partial_name": self.partial_name,
            "index": self.index,
        }
        if self.parent_locator:
            d["parent_locator"] = self.parent_locator.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> UiaElementLocator:
        parent_loc = None
        if "parent_locator" in data and isinstance(data["parent_locator"], dict):
            parent_loc = cls.from_dict(data["parent_locator"])
        return cls(
            automation_id=data.get("automation_id"),
            name=data.get("name"),
            control_type=data.get("control_type"),
            class_name=data.get("class_name"),
            partial_name=data.get("partial_name"),
            parent_locator=parent_loc,
            index=int(data.get("index", 0)),
        )


@dataclass
class UiaElementInfo:
    """Serializable snapshot of an element's accessibility and visual state."""
    automation_id: str = ""
    name: str = ""
    control_type: str = ""
    class_name: str = ""
    bounding_rect: Dict[str, int] = field(default_factory=lambda: {"left": 0, "top": 0, "right": 0, "bottom": 0})
    is_enabled: bool = True
    is_visible: bool = True
    supported_patterns: List[str] = field(default_factory=list)
    current_value: Optional[str] = None
    toggle_state: Optional[int] = None   # 0 = Off, 1 = On, 2 = Indeterminate
    is_selected: Optional[bool] = None
    children: List[UiaElementInfo] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["children"] = [c.to_dict() if isinstance(c, UiaElementInfo) else c for c in self.children]
        return d
