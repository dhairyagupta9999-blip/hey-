"""Agent Router V2 for Hey Jev Orchestrator.

Combines deterministic rule matching with a multi-factor CapabilityScorer:
evaluates capability overlap, availability, health, cost, latency, reliability,
permissions, and provider preference. Provides clean interface for future LLMRouter.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent_capability import AgentCapabilityCard
from agent_registry import AgentMetadata, AgentRegistry
from agent_task import AgentTask, TaskPermission


@dataclass
class ScoredCandidate:
    """Agent candidate with detailed scoring breakdown."""
    agent_name: str
    total_score: float
    capability_score: float
    availability_score: float
    cost_score: float
    latency_score: float
    reliability_score: float
    permission_score: float
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "total_score": round(self.total_score, 3),
            "capability_score": round(self.capability_score, 3),
            "availability_score": round(self.availability_score, 3),
            "cost_score": round(self.cost_score, 3),
            "latency_score": round(self.latency_score, 3),
            "reliability_score": round(self.reliability_score, 3),
            "permission_score": round(self.permission_score, 3),
            "rationale": self.rationale,
        }


@dataclass
class RouterDecisionV2:
    """Comprehensive routing outcome with primary agent and scored fallbacks."""
    selected_agent: str
    score: float
    reason: str
    scored_candidates: List[ScoredCandidate] = field(default_factory=list)
    fallback_agents: List[str] = field(default_factory=list)
    strategy_used: str = "rule_matched"  # "rule_matched", "capability_scored", "default_fallback"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_agent": self.selected_agent,
            "score": round(self.score, 3),
            "reason": self.reason,
            "strategy_used": self.strategy_used,
            "fallback_agents": self.fallback_agents,
            "scored_candidates": [c.to_dict() for c in self.scored_candidates],
        }


class CapabilityScorer:
    """Multi-factor scoring engine evaluating agent fitness for a task."""

    COST_WEIGHTS = {"free": 1.0, "low": 0.85, "medium": 0.60, "high": 0.30}
    LATENCY_WEIGHTS = {"fast": 1.0, "medium": 0.75, "slow": 0.40}
    AVAILABILITY_WEIGHTS = {
        "available": 1.0,
        "degraded": 0.50,
        "rate_limited": 0.10,
        "unavailable": 0.0,
        "auth_required": 0.0,
    }

    def score_candidate(
        self,
        task: AgentTask,
        meta: AgentMetadata,
        required_capabilities: Optional[List[str]] = None,
    ) -> ScoredCandidate:
        req_caps = required_capabilities or []
        if "required_capability" in task.context:
            req_caps.append(str(task.context["required_capability"]))

        # 1. Capability Overlap
        if req_caps:
            agent_caps_lower = {c.lower() for c in meta.capabilities}
            matches = sum(1 for rc in req_caps if rc.lower() in agent_caps_lower)
            cap_score = matches / max(len(req_caps), 1)
        else:
            # Baseline keyword overlap with goal
            goal_words = set(re.findall(r"\w+", task.goal.lower()))
            agent_words = set(re.findall(r"\w+", " ".join(meta.capabilities).lower()))
            overlap = len(goal_words & agent_words)
            cap_score = min(1.0, overlap * 0.25) if overlap > 0 else 0.50

        # 2. Availability & Health
        avail_score = self.AVAILABILITY_WEIGHTS.get(meta.availability.lower(), 0.0)
        if meta.health_status == "unhealthy":
            avail_score = 0.0
        elif meta.health_status == "degraded":
            avail_score *= 0.5

        # 3. Cost Score (prefer cheaper)
        cost_score = self.COST_WEIGHTS.get(meta.cost.lower(), 0.5)

        # 4. Latency Score
        latency_score = self.LATENCY_WEIGHTS.get(meta.speed.lower(), 0.5)

        # 5. Reliability Score
        rel_score = float(meta.reliability)

        # 6. Permissions Satisfaction
        if task.permissions:
            missing = [p for p in task.permissions if p not in meta.permissions]
            perm_score = 1.0 if not missing else max(0.0, 1.0 - (len(missing) / len(task.permissions)))
        else:
            perm_score = 1.0

        # Weighted composite score
        # 35% capability, 25% availability, 15% reliability, 10% permission, 10% cost, 5% latency
        total = (
            (cap_score * 0.35)
            + (avail_score * 0.25)
            + (rel_score * 0.15)
            + (perm_score * 0.10)
            + (cost_score * 0.10)
            + (latency_score * 0.05)
        )

        # If completely unavailable or missing critical permissions, penalize heavily
        if avail_score == 0.0:
            total = 0.0

        rationale = (
            f"Cap={cap_score:.2f}, Avail={avail_score:.2f}, Rel={rel_score:.2f}, "
            f"Perm={perm_score:.2f}, Cost={cost_score:.2f}"
        )

        return ScoredCandidate(
            agent_name=meta.name,
            total_score=total,
            capability_score=cap_score,
            availability_score=avail_score,
            cost_score=cost_score,
            latency_score=latency_score,
            reliability_score=rel_score,
            permission_score=perm_score,
            rationale=rationale,
        )


class AgentRouterV2:
    """Unified router employing rule matching followed by multi-factor capability scoring."""

    RULE_MATCHES = [
        # Coding & debugging
        (
            re.compile(r"\b(fix|bug|error|exception|python|traceback|code|refactor|build|implement|function|class|test)\b", re.IGNORECASE),
            "antigravity",
            ["coding", "debugging"],
        ),
        # Review & critique
        (
            re.compile(r"\b(review|critique|evaluate|analyze|architecture|verify plan|audit)\b", re.IGNORECASE),
            "claude",
            ["analysis", "review"],
        ),
        # Browser operations
        (
            re.compile(r"\b(browser|navigate|open tab|new tab|close tab|switch tab|open url|open website|fill form|submit form|webpage|web page|screenshot page)\b", re.IGNORECASE),
            "browser_agent",
            ["browser_use", "web_navigation"],
        ),
        # Deep research
        (
            re.compile(r"\b(research|hotels?|beachfront|find best|compare prices|compare laptops|compare these|lookup documentation|search online|investigate)\b", re.IGNORECASE),
            "research_agent",
            ["research", "web_investigation"],
        ),
        # Windows desktop use
        (
            re.compile(r"\b(wallpaper|dark mode|light mode|powershell|organize|downloads|clean up files|folder|rename files)\b", re.IGNORECASE),
            "windows_agent",
            ["computer_use", "windows_ui"],
        ),
    ]

    def __init__(self, scorer: Optional[CapabilityScorer] = None) -> None:
        self.scorer = scorer or CapabilityScorer()

    def route_task(self, task: AgentTask, registry: AgentRegistry) -> RouterDecisionV2:
        text = f"{task.goal} {task.description}".strip()
        all_agents = registry.list_agents()

        # Score all agents
        scored_candidates: List[ScoredCandidate] = []
        for meta in all_agents:
            scored = self.scorer.score_candidate(task, meta)
            scored_candidates.append(scored)

        scored_candidates.sort(key=lambda c: -c.total_score)

        # 1. Check direct Rule Matches
        rule_target: Optional[str] = None
        rule_reason: str = ""
        for pattern, target, caps in self.RULE_MATCHES:
            if pattern.search(text):
                target_meta = registry.get_agent(target)
                if target_meta and target_meta.availability != "unavailable" and target_meta.health_status != "unhealthy":
                    rule_target = target
                    rule_reason = f"Matched keyword pattern -> preferred specialist {target}"
                    break

        if rule_target:
            fallbacks = [c.agent_name for c in scored_candidates if c.agent_name != rule_target and c.total_score > 0.0]
            top_score = next((c.total_score for c in scored_candidates if c.agent_name == rule_target), 0.95)
            return RouterDecisionV2(
                selected_agent=rule_target,
                score=top_score,
                reason=rule_reason,
                scored_candidates=scored_candidates,
                fallback_agents=fallbacks,
                strategy_used="rule_matched",
            )

        # 2. Use top scored candidate if viable
        if scored_candidates and scored_candidates[0].total_score > 0.0:
            best = scored_candidates[0]
            fallbacks = [c.agent_name for c in scored_candidates[1:] if c.total_score > 0.0]
            return RouterDecisionV2(
                selected_agent=best.agent_name,
                score=best.total_score,
                reason=f"Top scored candidate ({best.rationale})",
                scored_candidates=scored_candidates,
                fallback_agents=fallbacks,
                strategy_used="capability_scored",
            )

        # 3. Default fallback
        fallback_name = "tier3_pc" if registry.get_agent("tier3_pc") else (all_agents[0].name if all_agents else "antigravity")
        return RouterDecisionV2(
            selected_agent=fallback_name,
            score=0.40,
            reason="Default fallback agent selected",
            scored_candidates=scored_candidates,
            fallback_agents=[],
            strategy_used="default_fallback",
        )

    def route(self, task: AgentTask, registry: AgentRegistry) -> RouterDecisionV2:
        """Alias for route_task."""
        return self.route_task(task, registry)


class LLMRouter(ABC):
    """Abstract interface for future LLM-based intelligent task router."""

    @abstractmethod
    def route_task(self, task: AgentTask, candidates: List[AgentCapabilityCard]) -> RouterDecisionV2:
        pass
