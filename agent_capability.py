"""Agent Capability Cards for Hey Jev Orchestrator.

Formalizes agent capability metadata, modalities, costs, latency classes,
A2A/MCP support flags, and operational limits.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from agent_task import TaskPermission


@dataclass
class AgentCapabilityCard:
    """Detailed formal specification of an agent's capabilities and constraints."""
    agent_id: str
    name: str
    provider: str
    version: str = "1.0.0"
    capabilities: List[str] = field(default_factory=list)
    input_types: List[str] = field(default_factory=lambda: ["text", "task_context"])
    output_types: List[str] = field(default_factory=lambda: ["text", "agent_result"])
    supported_modalities: List[str] = field(default_factory=lambda: ["text"])
    permissions: List[TaskPermission] = field(default_factory=list)
    cost_class: str = "low"  # free, low, medium, high
    latency_class: str = "fast"  # fast, medium, slow
    reliability: float = 0.95  # 0.0 - 1.0
    availability: str = "available"  # available, degraded, unavailable, rate_limited, auth_required
    supports_streaming: bool = True
    supports_background: bool = True
    supports_handoff: bool = True
    supports_a2a: bool = True
    supports_mcp: bool = True
    health_status: str = "healthy"  # healthy, degraded, unhealthy
    limits: Dict[str, Any] = field(default_factory=lambda: {"concurrency_limit": 1, "timeout_seconds": 60})
    description: str = ""

    def __post_init__(self) -> None:
        normalized_perms: List[TaskPermission] = []
        for p in self.permissions:
            if isinstance(p, str):
                try:
                    normalized_perms.append(TaskPermission(p))
                except ValueError:
                    pass
            elif isinstance(p, TaskPermission):
                normalized_perms.append(p)
        self.permissions = normalized_perms

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["permissions"] = [p.value if isinstance(p, TaskPermission) else str(p) for p in self.permissions]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentCapabilityCard:
        d = dict(data)
        if "permissions" in d:
            d["permissions"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permissions"]
            ]
        return cls(**d)
