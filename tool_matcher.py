"""Tool Matcher, Quality Scoring, and Historical Benchmarking for Hey Jev.

Evaluates and ranks candidate tools using deterministic multi-factor scoring:
- Capability match
- Implementation health
- Cost class
- Latency class
- Permissions satisfaction
- Rollback & verification support
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent_task import TaskPermission
from tool_model import RiskLevel, ToolDefinition, ToolHealth
from tool_registry import ToolRegistry


@dataclass
class ScoredTool:
    """Tool candidate scored against specific operational requirements."""
    tool: ToolDefinition
    quality_score: float
    capability_score: float
    health_score: float
    cost_score: float
    latency_score: float
    feature_score: float
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool.tool_id,
            "provider": self.tool.provider,
            "quality_score": round(self.quality_score, 3),
            "capability_score": round(self.capability_score, 3),
            "health_score": round(self.health_score, 3),
            "cost_score": round(self.cost_score, 3),
            "latency_score": round(self.latency_score, 3),
            "rationale": self.rationale,
        }


class ToolMatcher:
    """Matches and ranks tools against required capabilities and execution context."""

    COST_WEIGHTS = {"free": 1.0, "low": 0.85, "medium": 0.60, "high": 0.30}
    LATENCY_WEIGHTS = {"fast": 1.0, "medium": 0.75, "slow": 0.40}
    HEALTH_WEIGHTS = {
        ToolHealth.HEALTHY: 1.0,
        ToolHealth.DEGRADED: 0.5,
        ToolHealth.RATE_LIMITED: 0.2,
        ToolHealth.AUTH_REQUIRED: 0.1,
        ToolHealth.POLICY_BLOCKED: 0.0,
        ToolHealth.UNAVAILABLE: 0.0,
        ToolHealth.BROKEN: 0.0,
    }

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def match(
        self,
        required_capability: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        max_risk: Optional[RiskLevel] = None,
        prefer_offline: bool = False,
    ) -> List[ScoredTool]:
        """Find and rank all viable tools for a required capability."""
        candidates = self.registry.find_by_capability(required_capability)
        if not candidates:
            # Fallback search across tool descriptions and names
            candidates = self.registry.search(required_capability)

        granted_perms = set(caller_permissions or [])
        scored: List[ScoredTool] = []

        for tool in candidates:
            # 1. Capability match
            cap_lower = required_capability.lower()
            if any(cap_lower == c.lower() for c in tool.capabilities):
                cap_score = 1.0
            elif any(cap_lower in c.lower() for c in tool.capabilities):
                cap_score = 0.85
            else:
                cap_score = 0.50

            # 2. Health score
            health_score = self.HEALTH_WEIGHTS.get(tool.health, 0.0)

            # 3. Cost & Latency
            cost_score = self.COST_WEIGHTS.get(tool.cost_class.lower(), 0.5)
            latency_score = self.LATENCY_WEIGHTS.get(tool.latency_class.lower(), 0.5)

            # 4. Feature support bonuses
            feature_score = 0.0
            if tool.supports_verification:
                feature_score += 0.05
            if tool.supports_rollback:
                feature_score += 0.05
            if prefer_offline and tool.offline_capable:
                feature_score += 0.10

            # 5. Permission penalty if caller lacks required permissions
            if tool.permissions and not all(p in granted_perms for p in tool.permissions):
                perm_penalty = 0.5
            else:
                perm_penalty = 1.0

            # Composite Quality Score
            # 35% capability, 25% health, 15% cost, 15% latency, 10% features, scaled by permissions
            total = (
                (cap_score * 0.35)
                + (health_score * 0.25)
                + (cost_score * 0.15)
                + (latency_score * 0.15)
                + (feature_score * 0.10)
            ) * perm_penalty

            # Unhealthy tools receive zero
            if health_score == 0.0:
                total = 0.0

            rationale = (
                f"Cap={cap_score:.2f}, Health={health_score:.2f}, Cost={cost_score:.2f}, "
                f"Latency={latency_score:.2f}, PermOk={perm_penalty == 1.0}"
            )

            scored.append(
                ScoredTool(
                    tool=tool,
                    quality_score=total,
                    capability_score=cap_score,
                    health_score=health_score,
                    cost_score=cost_score,
                    latency_score=latency_score,
                    feature_score=feature_score,
                    rationale=rationale,
                )
            )

        scored.sort(key=lambda s: -s.quality_score)
        return scored


@dataclass
class ToolBenchmarkMetric:
    """Historical benchmark record for a tool."""
    tool_id: str
    call_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    latencies: List[float] = field(default_factory=list)

    @property
    def avg_latency_ms(self) -> float:
        return (self.total_duration_ms / self.call_count) if self.call_count > 0 else 0.0

    @property
    def success_rate(self) -> float:
        return (self.success_count / self.call_count) if self.call_count > 0 else 1.0


class ToolBenchmark:
    """Tracks historical execution latency and reliability metrics for tools."""

    def __init__(self) -> None:
        self.metrics: Dict[str, ToolBenchmarkMetric] = {}

    def record_call(self, tool_id: str, duration_ms: float, is_success: bool) -> None:
        """Record an execution measurement."""
        m = self.metrics.setdefault(tool_id, ToolBenchmarkMetric(tool_id=tool_id))
        m.call_count += 1
        if is_success:
            m.success_count += 1
        else:
            m.failure_count += 1
        m.total_duration_ms += duration_ms
        m.latencies.append(duration_ms)

    def get_metric(self, tool_id: str) -> Optional[ToolBenchmarkMetric]:
        return self.metrics.get(tool_id)
