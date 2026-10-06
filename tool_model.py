"""Tool Model, Risk Classification, and Structured Execution Results for Hey Jev.

Provides:
- RiskLevel enum (LOW, MEDIUM, HIGH, CRITICAL)
- ToolHealth enum (HEALTHY, DEGRADED, UNAVAILABLE, AUTH_REQUIRED, RATE_LIMITED, POLICY_BLOCKED, BROKEN)
- ToolDefinition schema with machine-readable input/output schemas
- ToolResult schema with dry-run support, verification evidence, and rollback metadata
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from agent_task import TaskPermission


class RiskLevel(str, Enum):
    """Risk tier of an operation determining authorization requirements."""
    LOW = "LOW"            # Read screen, list files, search web
    MEDIUM = "MEDIUM"      # Move files, change wallpaper, fill a form
    HIGH = "HIGH"          # Delete files, execute arbitrary commands, modify accounts
    CRITICAL = "CRITICAL"  # Financial transactions, credential changes, security policy changes


class ToolHealth(str, Enum):
    """Operational health state of a tool implementation."""
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    BROKEN = "BROKEN"


@dataclass
class ToolDefinition:
    """Provider-neutral formal specification of a concrete tool."""
    tool_id: str
    name: str
    description: str
    version: str = "1.0.0"
    provider: str = "builtin"
    category: str = "general"
    capabilities: List[str] = field(default_factory=list)
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    permissions: List[TaskPermission] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    cost_class: str = "free"  # "free", "low", "medium", "high"
    latency_class: str = "fast"  # "fast", "medium", "slow"
    availability: str = "available"  # "available", "degraded", "unavailable"
    health: ToolHealth = ToolHealth.HEALTHY
    offline_capable: bool = True
    requires_network: bool = False
    requires_auth: bool = False
    supports_dry_run: bool = False
    supports_rollback: bool = False
    supports_verification: bool = True
    handler_reference: Optional[Any] = None
    license: str = "MIT"
    source: str = "builtin"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.risk_level, str):
            try:
                self.risk_level = RiskLevel(self.risk_level.upper())
            except ValueError:
                self.risk_level = RiskLevel.LOW
        if isinstance(self.health, str):
            try:
                self.health = ToolHealth(self.health.upper())
            except ValueError:
                self.health = ToolHealth.HEALTHY

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
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "provider": self.provider,
            "category": self.category,
            "capabilities": list(self.capabilities),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "permissions": [p.value for p in self.permissions],
            "risk_level": self.risk_level.value,
            "cost_class": self.cost_class,
            "latency_class": self.latency_class,
            "availability": self.availability,
            "health": self.health.value,
            "offline_capable": self.offline_capable,
            "requires_network": self.requires_network,
            "requires_auth": self.requires_auth,
            "supports_dry_run": self.supports_dry_run,
            "supports_rollback": self.supports_rollback,
            "supports_verification": self.supports_verification,
            "license": self.license,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolDefinition:
        d = dict(data)
        if "permissions" in d:
            d["permissions"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permissions"]
            ]
        if "risk_level" in d and isinstance(d["risk_level"], str):
            d["risk_level"] = RiskLevel(d["risk_level"])
        if "health" in d and isinstance(d["health"], str):
            d["health"] = ToolHealth(d["health"])
        return cls(**d)


@dataclass
class ToolResult:
    """Normalized structured execution output emitted by a tool."""
    tool_id: str
    execution_id: str = field(default_factory=lambda: f"exec_{uuid.uuid4().hex[:8]}")
    status: str = "SUCCESS"  # "SUCCESS", "FAILURE", "BLOCKED", "DRY_RUN"
    input_summary: Dict[str, Any] = field(default_factory=dict)
    output: Any = None
    error: Optional[str] = None
    evidence: List[Any] = field(default_factory=list)
    before_state: Dict[str, Any] = field(default_factory=dict)
    after_state: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    duration: float = 0.0
    rollback_metadata: Dict[str, Any] = field(default_factory=dict)
    is_dry_run: bool = False

    @property
    def is_success(self) -> bool:
        return self.status in ("SUCCESS", "DRY_RUN") and self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolResult:
        return cls(**data)
