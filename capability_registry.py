"""Unified Capability Registry for Hey Jev (Phase 22).

Aggregates primitive tools and reusable skills into a single thread-safe
index of outcome-oriented capabilities. Automatically synchronizes with
Phase 15 ToolRegistry and SkillRegistry while supporting dynamic registration.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from skill_registry import SkillRegistry
from tool_model import RiskLevel, ToolHealth
from tool_registry import ToolRegistry


class CapabilityRegistry:
    """Thread-safe unified registry for all available capabilities."""

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        skill_executor: Optional[Any] = None,
    ) -> None:
        self._capabilities: Dict[str, CapabilityDefinition] = {}
        self._lock = threading.RLock()
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry
        self.skill_executor = skill_executor

        # Auto-sync if registries provided
        if self.tool_registry or self.skill_registry:
            self.sync_from_registries()

    def register(self, capability: CapabilityDefinition) -> None:
        """Register a unified capability."""
        with self._lock:
            self._capabilities[capability.capability_id] = capability

    def unregister(self, capability_id: str) -> bool:
        """Remove a capability from the registry."""
        with self._lock:
            return bool(self._capabilities.pop(capability_id, None))

    def get(self, capability_id: str) -> Optional[CapabilityDefinition]:
        """Retrieve capability by identifier."""
        with self._lock:
            return self._capabilities.get(capability_id)

    def list(
        self,
        capability_type: Optional[CapabilityType] = None,
        max_risk: Optional[RiskLevel] = None,
        only_available: bool = False,
    ) -> List[CapabilityDefinition]:
        """List registered capabilities matching optional filters."""
        with self._lock:
            res: List[CapabilityDefinition] = []
            risk_ranks = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
            max_rank = risk_ranks.get(max_risk, 4) if max_risk else 4

            for cap in self._capabilities.values():
                if capability_type and cap.capability_type != capability_type:
                    continue
                if only_available and not cap.is_available:
                    continue
                if risk_ranks.get(cap.risk_level, 1) > max_rank:
                    continue
                res.append(cap)
            return res

    def sync_from_registries(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        skill_executor: Optional[Any] = None,
    ) -> int:
        """Synchronize with underlying ToolRegistry and SkillRegistry. Returns total loaded."""
        t_reg = tool_registry or self.tool_registry
        s_reg = skill_registry or self.skill_registry
        s_exec = skill_executor or self.skill_executor

        count = 0
        with self._lock:
            if t_reg:
                for tool in t_reg.list():
                    handler = t_reg.get_handler(tool.tool_id)
                    cap = CapabilityDefinition.from_tool_definition(tool, handler=handler)
                    self._capabilities[cap.capability_id] = cap
                    count += 1

            if s_reg:
                for skill in s_reg.list():
                    cap = CapabilityDefinition.from_skill_definition(skill, executor=s_exec)
                    self._capabilities[cap.capability_id] = cap
                    count += 1

        return count

    def find_by_permission(self, permission: TaskPermission) -> List[CapabilityDefinition]:
        """Find capabilities requiring a specific permission."""
        with self._lock:
            return [c for c in self._capabilities.values() if permission in c.permissions]

    def count(self) -> int:
        """Return total number of registered capabilities."""
        with self._lock:
            return len(self._capabilities)
