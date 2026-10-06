"""Agent Router for Hey Jev Orchestrator.

Provides deterministic routing infrastructure to select the best agent
for a given task goal, required capabilities, permissions, and health status.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_registry import AgentMetadata, AgentRegistry
from agent_task import AgentTask, TaskPermission


@dataclass
class RoutingDecision:
    """Outcome of a task routing evaluation."""
    selected_agent: str
    fallback_agents: List[str] = field(default_factory=list)
    score: float = 1.0
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class TaskRouter(ABC):
    """Abstract interface for task routing strategies."""

    @abstractmethod
    def route_task(self, task: AgentTask, registry: AgentRegistry) -> RoutingDecision:
        """Route an AgentTask to the most suitable registered agent."""
        pass


class DeterministicAgentRouter(TaskRouter):
    """Deterministic rule- and capability-based task router."""

    # Keyword to capability mappings
    ROUTING_RULES = [
        # Coding & debugging
        (
            re.compile(r"\b(fix|bug|error|exception|python|traceback|code|refactor|build|implement|function|class|test)\b", re.IGNORECASE),
            "coding",
            "antigravity",
        ),
        # Review & analysis
        (
            re.compile(r"\b(review|critique|evaluate|analyze|architecture|verify plan|audit)\b", re.IGNORECASE),
            "review",
            "claude",
        ),
        # Research & web lookup
        (
            re.compile(r"\b(research|hotels?|beachfront|find best|compare prices|web|lookup documentation|search online)\b", re.IGNORECASE),
            "research",
            "research_agent",
        ),
        # Direct PC UI control
        (
            re.compile(r"\b(open picture|fourth picture|click|type into|volume|mute|maximize|switch window|launch app)\b", re.IGNORECASE),
            "pc_control",
            "tier3_pc",
        ),
        # Windows computer use, wallpaper, settings, powershell, and filesystem organization
        (
            re.compile(r"\b(wallpaper|dark mode|light mode|powershell|organize|downloads|clean up files|move files|folder|rename files|archive)\b", re.IGNORECASE),
            "computer_use",
            "windows_agent",
        ),
    ]

    def route_task(self, task: AgentTask, registry: AgentRegistry) -> RoutingDecision:
        """Evaluate task goal and permissions against registered agents."""
        text = f"{task.goal} {task.description}".strip()

        matched_primary: Optional[str] = None
        match_reason: str = ""

        # 1. Check regex rules
        for pattern, cap_name, target_agent in self.ROUTING_RULES:
            if pattern.search(text):
                agent = registry.get_agent(target_agent)
                if agent and agent.availability != "offline" and agent.health_status != "unhealthy":
                    matched_primary = target_agent
                    match_reason = f"Matched keyword pattern for capability '{cap_name}' -> {target_agent}"
                    break

        # 2. Check explicitly requested capabilities in task context
        if not matched_primary and "required_capability" in task.context:
            req_cap = str(task.context["required_capability"]).lower()
            candidates = registry.find_by_capability(req_cap)
            for cand in candidates:
                if cand.availability != "offline" and cand.health_status != "unhealthy":
                    matched_primary = cand.name
                    match_reason = f"Matched explicit required capability '{req_cap}' -> {cand.name}"
                    break

        # 3. Default fallback if no match found
        if not matched_primary:
            # Fall back to antigravity if coding-like, or tier3_pc
            if "code" in text.lower():
                matched_primary = "antigravity"
                match_reason = "Default coding fallback -> antigravity"
            else:
                matched_primary = "tier3_pc"
                match_reason = "Default general assistant fallback -> tier3_pc"

        # Check required permissions
        selected_meta = registry.get_agent(matched_primary)
        if selected_meta and task.permissions:
            missing_perms = [p for p in task.permissions if p not in selected_meta.permissions]
            if missing_perms:
                match_reason += f" (Note: Agent lacks permissions: {[p.value for p in missing_perms]})"

        # Determine fallbacks
        all_agents = registry.list_agents(lambda a: a.name != matched_primary and a.availability != "offline")
        fallbacks = [a.name for a in sorted(all_agents, key=lambda a: -a.reliability)]

        return RoutingDecision(
            selected_agent=matched_primary,
            fallback_agents=fallbacks,
            score=0.95 if selected_meta else 0.50,
            reason=match_reason,
            metadata={"evaluated_candidates": len(all_agents) + 1},
        )
