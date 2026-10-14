"""Swarm Planner and Minimum-Sufficient Delegation Engine for Hey Jev.

Analyzes goals, context, and available agents/skills/tools to construct
a dependency-aware SwarmDefinition using the minimum number of specialist
workers necessary. Enforces hardware limits (max_local_agents = 1,
max_spawn_depth = 1) and dynamic role synthesis.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_registry import AgentMetadata, AgentRegistry
from agent_task import TaskPermission
from swarm_model import (
    AgentTrustLevel,
    CostMode,
    SwarmBudget,
    SwarmDefinition,
    SwarmStatus,
    SwarmStrategy,
    SwarmWorker,
    WorkerLocation,
    WorkerStatus,
)


class SwarmPlanner:
    """Intelligent dependency-aware planner that decomposes goals into minimal specialist swarms."""

    ROLE_CAPABILITY_MAP: Dict[str, List[str]] = {
        "RESEARCHER": ["research", "web_investigation", "synthesis", "evidence_collection"],
        "CODER": ["coding", "debugging", "repository_editing", "code_generation"],
        "REVIEWER": ["review", "critique", "code_review", "analysis"],
        "TESTER": ["testing", "verification", "diagnostics", "command_execution"],
        "FACT_CHECKER": ["fact_checking", "evidence_collection", "synthesis"],
        "WINDOWS_OPERATOR": ["pc_control", "windows_gui", "hotkeys", "application_launch"],
        "BROWSER_OPERATOR": ["browser_use", "navigation", "web_interaction"],
        "SECURITY_REVIEWER": ["security_audit", "permission_check", "secret_scan"],
        "DATA_ANALYST": ["data_analysis", "table_processing", "metric_evaluation"],
        "SYNTHESIZER": ["synthesis", "adjudication", "result_merging"],
    }

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        max_agents_per_mission: int = 5,
        default_cost_mode: CostMode = CostMode.FREE_FIRST,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.max_agents_per_mission = max(1, max_agents_per_mission)
        self.default_cost_mode = default_cost_mode

    def plan_swarm(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        available_agents: Optional[List[str]] = None,
        available_skills: Optional[List[str]] = None,
        available_tools: Optional[List[str]] = None,
        permissions: Optional[List[TaskPermission]] = None,
        resource_limits: Optional[Dict[str, Any]] = None,
        strategy_override: Optional[SwarmStrategy] = None,
        cost_mode: Optional[CostMode] = None,
        max_agents: Optional[int] = None,
    ) -> SwarmDefinition:
        """Analyze the user's objective and construct an optimal SwarmDefinition."""
        ctx = dict(context or {})
        limits = dict(resource_limits or {})
        effective_cost_mode = cost_mode or self.default_cost_mode
        agent_ceiling = min(
            self.max_agents_per_mission,
            max_agents or limits.get("max_agents", self.max_agents_per_mission),
        )

        # 1. Complexity Assessment & Worker Allocation Heuristic
        complexity, estimated_workers, chosen_strategy = self._assess_complexity_and_strategy(goal, ctx)
        if strategy_override:
            chosen_strategy = strategy_override

        # Cap workers by minimum-sufficient allocation rule
        worker_count = max(1, min(estimated_workers, agent_ceiling))

        # 2. Dynamic Role Generation
        roles = self._determine_roles(goal, ctx, chosen_strategy, worker_count)

        # 3. Construct Swarm Definition
        swarm_id = f"swarm_{uuid.uuid4().hex[:10]}"
        mission_id = ctx.get("mission_id", f"miss_{uuid.uuid4().hex[:10]}")

        swarm = SwarmDefinition(
            swarm_id=swarm_id,
            mission_id=mission_id,
            objective=goal,
            strategy=chosen_strategy,
            max_agents=agent_ceiling,
            max_local_agents=limits.get("max_local_agents", 1),
            max_remote_agents=limits.get("max_remote_agents", 4),
            parallelism=min(worker_count, limits.get("parallelism", 2)),
            budget=SwarmBudget(
                max_cost=float(limits.get("max_cost", 0.0 if effective_cost_mode == CostMode.FREE_FIRST else 1.0)),
                max_requests=int(limits.get("max_requests", 15)),
                max_tokens=int(limits.get("max_tokens", 50000)),
                max_wall_time=float(limits.get("max_wall_time", 180.0)),
                max_workers=agent_ceiling,
            ),
            timeout=float(limits.get("timeout", 180.0)),
            cost_mode=effective_cost_mode,
            metadata={
                "complexity": complexity,
                "goal": goal,
                "spawn_depth": 0,
                "max_spawn_depth": 1,
            },
        )

        # 4. Map Roles to Workers & Allocate Providers
        self._populate_workers_and_dependencies(
            swarm=swarm,
            roles=roles,
            goal=goal,
            ctx=ctx,
            available_agents=available_agents,
        )

        return swarm

    def _assess_complexity_and_strategy(
        self, goal: str, context: Dict[str, Any]
    ) -> Tuple[str, int, SwarmStrategy]:
        """Classify goal complexity and choose the best coordination topology."""
        g = goal.lower()

        # Check explicit keywords
        if any(w in g for w in ["debate", "opposing views", "argue", "compare viewpoints"]):
            return "complex", 2, SwarmStrategy.DEBATE

        if any(w in g for w in ["best solution", "compete", "contest", "benchmark implementations", "compare approaches"]):
            return "complex", 3, SwarmStrategy.COMPETITION

        if any(w in g for w in ["panel", "specialist panel", "multi-expert", "multi perspective"]):
            return "complex", 3, SwarmStrategy.SPECIALIST_PANEL

        if any(w in g for w in ["research deeply", "deep research", "investigate topic", "comprehensive survey"]) or ("research" in g and "deep" in g):
            return "complex", 3, SwarmStrategy.MAP_REDUCE

        if any(w in g for w in ["fix project", "fix this project", "diagnose failure", "debug and test", "triage"]):
            return "complex", 3, SwarmStrategy.PIPELINE

        if any(w in g for w in ["review", "critique", "audit with review"]):
            return "moderate", 2, SwarmStrategy.REVIEW_LOOP

        if any(w in g for w in ["parallel", "in parallel", "simultaneously", "at the same time"]):
            return "moderate", 2, SwarmStrategy.PARALLEL

        # Fallback length/complexity heuristic
        word_count = len(goal.split())
        if word_count < 8 and not any(w in g for w in ["and", "then", "after", "also"]):
            return "simple", 1, SwarmStrategy.SEQUENTIAL

        if word_count < 20:
            return "moderate", 2, SwarmStrategy.SEQUENTIAL

        return "complex", 3, SwarmStrategy.PIPELINE

    def _determine_roles(
        self, goal: str, context: Dict[str, Any], strategy: SwarmStrategy, count: int
    ) -> List[str]:
        """Derive specialist roles matching the selected topology."""
        g = goal.lower()

        if strategy == SwarmStrategy.COMPETITION:
            return ["CODER", "CODER", "CODER"][:count]

        if strategy == SwarmStrategy.DEBATE:
            return ["RESEARCHER", "REVIEWER"][:count]

        if strategy == SwarmStrategy.SPECIALIST_PANEL:
            return ["RESEARCHER", "SECURITY_REVIEWER", "DATA_ANALYST"][:count]

        if strategy == SwarmStrategy.MAP_REDUCE:
            return ["RESEARCHER", "RESEARCHER", "SYNTHESIZER"][:count]

        if strategy == SwarmStrategy.REVIEW_LOOP:
            return ["CODER", "REVIEWER"][:count]

        if strategy == SwarmStrategy.PIPELINE:
            if "diagnose" in g or "failure" in g or "bug" in g:
                pipeline_roles = ["RESEARCHER", "CODER", "TESTER"]
            elif "project" in g:
                pipeline_roles = ["WINDOWS_OPERATOR", "CODER", "TESTER"]
            else:
                pipeline_roles = ["RESEARCHER", "CODER", "REVIEWER"]
            return pipeline_roles[:count]

        if strategy == SwarmStrategy.PARALLEL:
            if "research" in g:
                return ["RESEARCHER", "RESEARCHER"][:count]
            return ["CODER", "REVIEWER"][:count]

        # Sequential or default
        if "research" in g or "find" in g or "search" in g:
            return (["RESEARCHER"] * count)
        if "fix" in g or "code" in g or "refactor" in g:
            return (["CODER", "REVIEWER"][:count])
        if "browser" in g or "web" in g:
            return (["BROWSER_OPERATOR"] * count)
        if "pc" in g or "window" in g or "app" in g:
            return (["WINDOWS_OPERATOR"] * count)

        return ["CODER"] * count

    def _populate_workers_and_dependencies(
        self,
        swarm: SwarmDefinition,
        roles: List[str],
        goal: str,
        ctx: Dict[str, Any],
        available_agents: Optional[List[str]],
    ) -> None:
        """Instantiate SwarmWorker objects and wire DAG dependencies."""
        assigned_local_count = 0
        workers: List[SwarmWorker] = []

        for idx, role in enumerate(roles):
            # Select agent and provider for role
            agent_id, provider, location, trust = self._select_agent_for_role(
                role, ctx, available_agents, assigned_local_count
            )
            if location == WorkerLocation.LOCAL:
                assigned_local_count += 1

            worker_id = f"wkr_{role.lower()}_{idx+1}_{uuid.uuid4().hex[:4]}"
            worker = SwarmWorker(
                worker_id=worker_id,
                agent_id=agent_id,
                role=role,
                task_id=f"task_{swarm.swarm_id[:6]}_{idx+1}",
                provider=provider,
                location=location,
                status=WorkerStatus.QUEUED,
                capabilities=self.ROLE_CAPABILITY_MAP.get(role, ["general"]),
                permission_profile=self._get_permissions_for_role(role),
                trust_level=trust,
                cost_estimate=0.0 if location == WorkerLocation.LOCAL or swarm.cost_mode == CostMode.FREE_FIRST else 0.01,
                latency_estimate=1.0 if location == WorkerLocation.LOCAL else 0.5,
                idempotency_key=f"idem_{swarm.swarm_id}_{idx+1}",
            )
            workers.append(worker)

        # Build dependencies according to strategy
        dependencies: Dict[str, List[str]] = {}
        if swarm.strategy in (SwarmStrategy.SEQUENTIAL, SwarmStrategy.PIPELINE, SwarmStrategy.REVIEW_LOOP):
            for i in range(1, len(workers)):
                dependencies[workers[i].worker_id] = [workers[i-1].worker_id]
        elif swarm.strategy == SwarmStrategy.MAP_REDUCE and len(workers) > 1:
            # First N-1 are parallel mappers, last one is reducer dependent on all mappers
            mapper_ids = [w.worker_id for w in workers[:-1]]
            dependencies[workers[-1].worker_id] = mapper_ids
        elif swarm.strategy in (SwarmStrategy.PARALLEL, SwarmStrategy.COMPETITION, SwarmStrategy.SPECIALIST_PANEL):
            # All workers are independent, zero prerequisites
            pass
        elif swarm.strategy == SwarmStrategy.DEBATE and len(workers) >= 2:
            # Both debaters run concurrently
            pass

        swarm.workers = workers
        swarm.dependencies = dependencies

    def _select_agent_for_role(
        self,
        role: str,
        ctx: Dict[str, Any],
        available_agents: Optional[List[str]],
        assigned_local_count: int,
    ) -> Tuple[str, str, WorkerLocation, AgentTrustLevel]:
        """Find the most appropriate registered agent while respecting max_local_agents = 1."""
        role_map = {
            "RESEARCHER": ("research_agent", "web_search", WorkerLocation.REMOTE, AgentTrustLevel.VERIFIED_REMOTE),
            "CODER": ("antigravity", "antigravity", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED),
            "REVIEWER": ("claude", "anthropic", WorkerLocation.REMOTE, AgentTrustLevel.VERIFIED_REMOTE),
            "TESTER": ("antigravity", "antigravity", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED),
            "FACT_CHECKER": ("research_agent", "web_search", WorkerLocation.REMOTE, AgentTrustLevel.VERIFIED_REMOTE),
            "WINDOWS_OPERATOR": ("tier3_pc", "hey_jev", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED),
            "BROWSER_OPERATOR": ("browser_agent", "cdp_browser", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED),
            "SECURITY_REVIEWER": ("claude", "anthropic", WorkerLocation.REMOTE, AgentTrustLevel.VERIFIED_REMOTE),
            "DATA_ANALYST": ("research_agent", "web_search", WorkerLocation.REMOTE, AgentTrustLevel.VERIFIED_REMOTE),
            "SYNTHESIZER": ("jev_synthesizer", "hey_jev", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED),
        }

        agent_id, provider, location, trust = role_map.get(
            role, ("antigravity", "antigravity", WorkerLocation.LOCAL, AgentTrustLevel.LOCAL_TRUSTED)
        )

        # Enforce max_local_agents = 1: if local slot is already taken, convert secondary to remote
        if location == WorkerLocation.LOCAL and assigned_local_count >= 1:
            location = WorkerLocation.REMOTE
            provider = "openai_compatible"
            trust = AgentTrustLevel.VERIFIED_REMOTE

        return agent_id, provider, location, trust

    def _get_permissions_for_role(self, role: str) -> List[TaskPermission]:
        """Assign least-privilege permissions based on role."""
        if role in ("CODER", "TESTER"):
            return [TaskPermission.READ_FILES, TaskPermission.WRITE_FILES, TaskPermission.EXECUTE_COMMAND]
        if role in ("RESEARCHER", "FACT_CHECKER"):
            return [TaskPermission.NETWORK_ACCESS]
        if role == "WINDOWS_OPERATOR":
            return [TaskPermission.CHANGE_SETTINGS, TaskPermission.EXECUTE_COMMAND]
        if role == "BROWSER_OPERATOR":
            return [TaskPermission.CONTROL_BROWSER, TaskPermission.NETWORK_ACCESS]
        return [TaskPermission.READ_FILES]
