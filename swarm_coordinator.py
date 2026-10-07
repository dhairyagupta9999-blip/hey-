"""Swarm Coordinator and High-Level Orchestration Facade for Hey Jev.

Ties together:
- SwarmPlanner (minimum-sufficient worker delegation & topology selection)
- SwarmEngine (DAG execution, resource governance, and persistence)
- ProviderGateway (multi-provider routing, budget gating, and secret scrubbing)
- SqliteMemoryStore (recording durable swarm lessons and conclusions)
- NotificationManager & BackgroundTaskManager (asynchronous swarm background tasks)
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_result import AgentResult, AgentResultStatus
from consensus_engine import ConsensusEngine, SwarmResultMerger
from event_bus import EventBus
from memory_model import EpistemicStatus, MemoryRecord, MemorySensitivity, MemorySource, MemoryType
from memory_store import SqliteMemoryStore
from notification_manager import NotificationLevel, NotificationManager
from provider_gateway import ProviderGateway
from resource_governor import ResourceGovernor
from swarm_engine import SwarmEngine, SwarmPersistenceManager
from swarm_model import CostMode, SwarmDefinition, SwarmStatus, SwarmStrategy
from swarm_planner import SwarmPlanner


class SwarmCoordinator:
    """High-level coordinator orchestrating the full swarm lifecycle from goal to verified result."""

    def __init__(
        self,
        planner: Optional[SwarmPlanner] = None,
        engine: Optional[SwarmEngine] = None,
        gateway: Optional[ProviderGateway] = None,
        memory_store: Optional[SqliteMemoryStore] = None,
        notification_manager: Optional[NotificationManager] = None,
        event_bus: Optional[EventBus] = None,
        governor: Optional[ResourceGovernor] = None,
    ) -> None:
        self.gateway = gateway or ProviderGateway()
        self.planner = planner or SwarmPlanner()
        self.governor = governor or ResourceGovernor()
        self.event_bus = event_bus or EventBus()
        self.persistence = SwarmPersistenceManager()
        self.engine = engine or SwarmEngine(
            gateway=self.gateway,
            event_bus=self.event_bus,
            governor=self.governor,
            persistence=self.persistence,
        )
        self.memory_store = memory_store
        self.notification_manager = notification_manager

    def execute_goal(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        strategy_override: Optional[SwarmStrategy] = None,
        cost_mode: Optional[CostMode] = None,
        max_agents: Optional[int] = None,
    ) -> Tuple[SwarmDefinition, AgentResult]:
        """Plan and execute a multi-agent swarm to accomplish the user's goal."""
        ctx = dict(context or {})

        # 1. Plan minimal sufficient swarm
        swarm = self.planner.plan_swarm(
            goal=goal,
            context=ctx,
            strategy_override=strategy_override,
            cost_mode=cost_mode,
            max_agents=max_agents,
        )

        # 2. Execute swarm
        result = self.engine.execute_swarm(swarm)

        # 3. Durable Memory Persistence
        if self.memory_store and result.is_success:
            try:
                mem = MemoryRecord(
                    type=MemoryType.MISSION_SUMMARY,
                    content=result.summary or f"Swarm completed {goal}",
                    summary=f"Swarm [{swarm.strategy.value}]: {goal}",
                    epistemic_status=EpistemicStatus.FACT,
                    source=MemorySource.AGENT,
                    sensitivity=MemorySensitivity.NORMAL,
                    tags=["swarm", swarm.strategy.value.lower()],
                )
                self.memory_store.add(mem)
            except Exception:
                pass

        # 4. Notification dispatch
        if self.notification_manager:
            level = NotificationLevel.HIGH if not result.is_success else NotificationLevel.NORMAL
            msg = f"Swarm {swarm.status.value}: {result.summary[:100]}"
            self.notification_manager.notify(
                title=f"Swarm: {goal[:30]}",
                message=msg,
                level=level,
            )

        return swarm, result

    def handle_background_task(self, task_def: Any) -> Dict[str, Any]:
        """Callable adapter for Phase 17 BackgroundTaskManager execution."""
        payload = getattr(task_def, "action_payload", {}) or {}
        goal = payload.get("goal") or getattr(task_def, "name", "Background Swarm")
        strat_str = payload.get("strategy")
        strategy = SwarmStrategy(strat_str) if strat_str else None

        swarm, res = self.execute_goal(
            goal=goal,
            context=payload.get("context", {}),
            strategy_override=strategy,
            cost_mode=CostMode.FREE_FIRST,
        )

        return {
            "swarm_id": swarm.swarm_id,
            "status": swarm.status.value,
            "result": res.to_dict(),
            "summary": res.summary,
        }
