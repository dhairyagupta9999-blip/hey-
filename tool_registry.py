"""Tool Registry for Hey Jev.

Manages dynamic tool registration, multi-provider implementations,
capability-indexed lookups, health state tracking, and permission inspection.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from tool_model import RiskLevel, ToolDefinition, ToolHealth


class ToolRegistry:
    """Thread-safe dynamic registry of available tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._handlers: Dict[str, Callable] = {}
        self._lock = threading.RLock()

    def register(self, tool_def: ToolDefinition, handler: Optional[Callable] = None) -> None:
        """Register a tool definition and optional execution handler."""
        with self._lock:
            self._tools[tool_def.tool_id] = tool_def
            if handler is not None:
                self._handlers[tool_def.tool_id] = handler
            elif tool_def.handler_reference is not None and callable(tool_def.handler_reference):
                self._handlers[tool_def.tool_id] = tool_def.handler_reference

    def unregister(self, tool_id: str) -> bool:
        """Remove a tool from the registry."""
        with self._lock:
            found = tool_id in self._tools
            self._tools.pop(tool_id, None)
            self._handlers.pop(tool_id, None)
            return found

    def get(self, tool_id: str) -> Optional[ToolDefinition]:
        """Fetch tool definition by ID."""
        with self._lock:
            return self._tools.get(tool_id)

    def get_handler(self, tool_id: str) -> Optional[Callable]:
        """Fetch bound execution handler for a tool."""
        with self._lock:
            return self._handlers.get(tool_id)

    def list(self, category: Optional[str] = None) -> List[ToolDefinition]:
        """List all registered tools, optionally filtered by category."""
        with self._lock:
            if category is None:
                return list(self._tools.values())
            return [t for t in self._tools.values() if t.category.lower() == category.lower()]

    def search(self, query: str) -> List[ToolDefinition]:
        """Search tools by name, description, category, or capability."""
        q_lower = query.lower()
        words = set(re.findall(r"\w+", q_lower))
        with self._lock:
            matches: List[Tuple[float, ToolDefinition]] = []
            for t in self._tools.values():
                score = 0.0
                if q_lower in t.tool_id.lower():
                    score += 5.0
                if q_lower in t.name.lower():
                    score += 4.0
                if any(q_lower in c.lower() for c in t.capabilities):
                    score += 3.0
                if q_lower in t.category.lower():
                    score += 2.0
                if q_lower in t.description.lower():
                    score += 1.0

                # Keyword overlap
                text = f"{t.name} {t.description} {' '.join(t.capabilities)} {t.category}".lower()
                overlap = sum(1 for w in words if w in text)
                score += overlap * 0.5

                if score > 0.0:
                    matches.append((score, t))

            matches.sort(key=lambda item: -item[0])
            return [m[1] for m in matches]

    def find_by_capability(self, capability: str) -> List[ToolDefinition]:
        """Find all tools implementing a specific capability."""
        cap_lower = capability.lower()
        with self._lock:
            return [
                t for t in self._tools.values()
                if any(cap_lower in c.lower() for c in t.capabilities)
                or cap_lower == t.category.lower()
                or cap_lower in t.tool_id.lower()
            ]

    def find_by_permission(self, permission: TaskPermission) -> List[ToolDefinition]:
        """Find all tools requiring a given permission."""
        with self._lock:
            return [t for t in self._tools.values() if permission in t.permissions]

    def find_by_provider(self, provider: str) -> List[ToolDefinition]:
        """Find all tools supplied by a provider."""
        p_lower = provider.lower()
        with self._lock:
            return [t for t in self._tools.values() if t.provider.lower() == p_lower]

    def find_by_risk(self, max_risk: RiskLevel) -> List[ToolDefinition]:
        """Find tools whose risk does not exceed max_risk."""
        risk_rank = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
        max_val = risk_rank.get(max_risk, 1)
        with self._lock:
            return [
                t for t in self._tools.values()
                if risk_rank.get(t.risk_level, 1) <= max_val
            ]

    def health_check(self, tool_id: str) -> ToolHealth:
        """Inspect health status of a tool."""
        with self._lock:
            t = self._tools.get(tool_id)
            return t.health if t else ToolHealth.UNAVAILABLE

    def update_health(self, tool_id: str, health: ToolHealth) -> bool:
        """Update the health status of a tool implementation."""
        with self._lock:
            t = self._tools.get(tool_id)
            if t:
                t.health = health
                return True
            return False

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
