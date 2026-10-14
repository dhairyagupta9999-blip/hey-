"""Swarm Model, Worker Definitions, and Status Snapshots for Hey Jev.

Defines:
- SwarmDefinition: Complete multi-agent swarm specification, DAG dependencies,
  budgets, and execution constraints.
- SwarmWorker: Individual specialist worker configuration, location, role, and telemetry.
- SwarmStatusSnapshot: Observability record for UI, CLI, and speech status.
- Trust, cost, and lifecycle enums.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union

from agent_result import AgentResult, AgentResultStatus
from agent_task import TaskPermission


class SwarmStatus(str, Enum):
    """Lifecycle statuses for a multi-agent swarm."""
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    REVIEWING = "REVIEWING"
    SYNTHESIZING = "SYNTHESIZING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"


class SwarmStrategy(str, Enum):
    """Explicit coordination strategies supported by the SwarmEngine."""
    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    PIPELINE = "PIPELINE"
    DEBATE = "DEBATE"
    SPECIALIST_PANEL = "SPECIALIST_PANEL"
    MAP_REDUCE = "MAP_REDUCE"
    COMPETITION = "COMPETITION"
    REVIEW_LOOP = "REVIEW_LOOP"


class WorkerLocation(str, Enum):
    """Execution locality of a worker."""
    LOCAL = "LOCAL"
    REMOTE = "REMOTE"


class WorkerStatus(str, Enum):
    """Operational health and execution states of a worker."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BLOCKED = "BLOCKED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"


class AgentTrustLevel(str, Enum):
    """Trust tier governing data access and tool execution."""
    LOCAL_TRUSTED = "LOCAL_TRUSTED"
    VERIFIED_REMOTE = "VERIFIED_REMOTE"
    COMMUNITY_REMOTE = "COMMUNITY_REMOTE"
    UNTRUSTED = "UNTRUSTED"


class CostMode(str, Enum):
    """Cost optimization policy for provider routing."""
    FREE_FIRST = "FREE_FIRST"
    BALANCED = "BALANCED"
    QUALITY_FIRST = "QUALITY_FIRST"
    LOCAL_ONLY = "LOCAL_ONLY"
    OFFLINE_ONLY = "OFFLINE_ONLY"


@dataclass
class SwarmBudget:
    """Operational and financial ceilings for a swarm."""
    max_cost: float = 0.0          # Cap in USD. 0.0 means free/local only
    max_requests: int = 15        # Max API calls across all workers
    max_tokens: int = 50000       # Max total tokens (prompt + completion)
    max_wall_time: float = 300.0  # Max total execution time in seconds
    max_workers: int = 5          # Absolute ceiling on specialist count

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwarmBudget:
        return cls(**data)


@dataclass
class ArtifactTrustMetadata:
    """Cryptographic and attribution metadata for artifacts emitted by workers."""
    artifact_id: str = field(default_factory=lambda: f"art_{uuid.uuid4().hex[:8]}")
    producer_agent: str = ""
    provider: str = ""
    trust_level: AgentTrustLevel = AgentTrustLevel.UNTRUSTED
    source_task_id: str = ""
    created_at: float = field(default_factory=time.time)
    checksum: Optional[str] = None
    verified_by: Optional[str] = None
    is_executable: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def compute_checksum(self, content_bytes: bytes) -> str:
        self.checksum = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
        return self.checksum

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["trust_level"] = self.trust_level.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ArtifactTrustMetadata:
        d = dict(data)
        if "trust_level" in d:
            d["trust_level"] = AgentTrustLevel(d["trust_level"])
        return cls(**d)


@dataclass
class SwarmWorker:
    """Individual specialist worker assigned to a swarm task."""
    worker_id: str = field(default_factory=lambda: f"wkr_{uuid.uuid4().hex[:8]}")
    agent_id: str = ""
    role: str = "WORKER"
    task_id: str = field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    provider: str = "antigravity"
    location: WorkerLocation = WorkerLocation.LOCAL
    status: WorkerStatus = WorkerStatus.QUEUED
    capabilities: List[str] = field(default_factory=list)
    permission_profile: List[TaskPermission] = field(default_factory=list)
    cost_estimate: float = 0.0
    latency_estimate: float = 1.0
    result: Optional[AgentResult] = None
    health: str = "HEALTHY"
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    trust_level: AgentTrustLevel = AgentTrustLevel.LOCAL_TRUSTED
    heartbeat_at: Optional[float] = None
    idempotency_key: str = field(default_factory=lambda: f"idem_{uuid.uuid4().hex[:12]}")
    attempt_id: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.location, str):
            self.location = WorkerLocation(self.location.upper())
        if isinstance(self.status, str):
            try:
                self.status = WorkerStatus(self.status.upper())
            except ValueError:
                self.status = WorkerStatus.QUEUED
        if isinstance(self.trust_level, str):
            self.trust_level = AgentTrustLevel(self.trust_level.upper())
        
        # Normalize permissions
        perms: List[TaskPermission] = []
        for p in self.permission_profile:
            if isinstance(p, str):
                try:
                    perms.append(TaskPermission(p))
                except ValueError:
                    pass
            elif isinstance(p, TaskPermission):
                perms.append(p)
        self.permission_profile = perms

    def record_heartbeat(self) -> None:
        self.heartbeat_at = time.time()

    def is_stale(self, timeout_seconds: float = 60.0) -> bool:
        if self.status != WorkerStatus.RUNNING or not self.heartbeat_at:
            return False
        return (time.time() - self.heartbeat_at) > timeout_seconds

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "agent_id": self.agent_id,
            "role": self.role,
            "task_id": self.task_id,
            "provider": self.provider,
            "location": self.location.value,
            "status": self.status.value,
            "capabilities": list(self.capabilities),
            "permission_profile": [p.value for p in self.permission_profile],
            "cost_estimate": self.cost_estimate,
            "latency_estimate": self.latency_estimate,
            "result": self.result.to_dict() if self.result and hasattr(self.result, "to_dict") else (self.result if isinstance(self.result, dict) else None),
            "health": self.health,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "trust_level": self.trust_level.value,
            "heartbeat_at": self.heartbeat_at,
            "idempotency_key": self.idempotency_key,
            "attempt_id": self.attempt_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwarmWorker:
        d = dict(data)
        if "location" in d:
            d["location"] = WorkerLocation(d["location"])
        if "status" in d:
            d["status"] = WorkerStatus(d["status"])
        if "trust_level" in d:
            d["trust_level"] = AgentTrustLevel(d["trust_level"])
        if "permission_profile" in d:
            d["permission_profile"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permission_profile"]
            ]
        if "result" in d and isinstance(d["result"], dict):
            d["result"] = AgentResult(**d["result"])
        return cls(**d)


@dataclass
class SwarmDefinition:
    """Specification of an orchestrated multi-agent swarm."""
    swarm_id: str = field(default_factory=lambda: f"swarm_{uuid.uuid4().hex[:10]}")
    mission_id: str = field(default_factory=lambda: f"miss_{uuid.uuid4().hex[:10]}")
    objective: str = ""
    workers: List[SwarmWorker] = field(default_factory=list)
    strategy: SwarmStrategy = SwarmStrategy.SEQUENTIAL
    max_agents: int = 5
    max_local_agents: int = 1
    max_remote_agents: int = 4
    parallelism: int = 2
    budget: SwarmBudget = field(default_factory=SwarmBudget)
    timeout: float = 180.0
    review_policy: Dict[str, Any] = field(default_factory=lambda: {"enabled": True, "max_rounds": 2})
    verification_policy: Dict[str, Any] = field(default_factory=lambda: {"enabled": True, "strict": True})
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    status: SwarmStatus = SwarmStatus.CREATED
    cost_mode: CostMode = CostMode.FREE_FIRST
    spent_cost: float = 0.0
    requests_made: int = 0
    tokens_used: int = 0
    dependencies: Dict[str, List[str]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = SwarmStatus(self.status.upper())
        if isinstance(self.strategy, str):
            self.strategy = SwarmStrategy(self.strategy.upper())
        if isinstance(self.cost_mode, str):
            self.cost_mode = CostMode(self.cost_mode.upper())
        if isinstance(self.budget, dict):
            self.budget = SwarmBudget.from_dict(self.budget)

    def get_worker(self, worker_id: str) -> Optional[SwarmWorker]:
        for w in self.workers:
            if w.worker_id == worker_id:
                return w
        return None

    def add_worker(self, worker: SwarmWorker, depends_on: Optional[List[str]] = None) -> None:
        self.workers.append(worker)
        if depends_on:
            self.dependencies[worker.worker_id] = list(depends_on)

    def can_worker_start(self, worker_id: str) -> bool:
        """True if all prerequisite workers have reached COMPLETED status."""
        prereqs = self.dependencies.get(worker_id, [])
        for pid in prereqs:
            parent = self.get_worker(pid)
            if not parent or parent.status != WorkerStatus.COMPLETED:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "swarm_id": self.swarm_id,
            "mission_id": self.mission_id,
            "objective": self.objective,
            "workers": [w.to_dict() for w in self.workers],
            "strategy": self.strategy.value,
            "max_agents": self.max_agents,
            "max_local_agents": self.max_local_agents,
            "max_remote_agents": self.max_remote_agents,
            "parallelism": self.parallelism,
            "budget": self.budget.to_dict(),
            "timeout": self.timeout,
            "review_policy": dict(self.review_policy),
            "verification_policy": dict(self.verification_policy),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status.value,
            "cost_mode": self.cost_mode.value,
            "spent_cost": self.spent_cost,
            "requests_made": self.requests_made,
            "tokens_used": self.tokens_used,
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwarmDefinition:
        d = dict(data)
        if "status" in d:
            d["status"] = SwarmStatus(d["status"])
        if "strategy" in d:
            d["strategy"] = SwarmStrategy(d["strategy"])
        if "cost_mode" in d:
            d["cost_mode"] = CostMode(d["cost_mode"])
        if "budget" in d and isinstance(d["budget"], dict):
            d["budget"] = SwarmBudget.from_dict(d["budget"])
        if "workers" in d and isinstance(d["workers"], list):
            d["workers"] = [
                SwarmWorker.from_dict(w) if isinstance(w, dict) else w
                for w in d["workers"]
            ]
        return cls(**d)


@dataclass
class SwarmStatusSnapshot:
    """Point-in-time observational snapshot of swarm execution."""
    mission_id: str
    swarm_id: str
    status: SwarmStatus
    strategy: SwarmStrategy
    total_workers: int
    active_workers: int
    queued_workers: int
    completed_workers: int
    failed_workers: int
    current_phase: str
    estimated_progress: float  # 0.0 to 1.0
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    cost_estimate: float = 0.0
    spent_cost: float = 0.0
    warnings: List[str] = field(default_factory=list)

    def summary_text(self) -> str:
        """Produce human- and voice-friendly summary string."""
        pct = int(self.estimated_progress * 100)
        lines = [
            f"Swarm [{self.swarm_id[:8]}] - {self.status.value} ({pct}%)",
            f"Strategy: {self.strategy.value} | Phase: {self.current_phase}",
            f"Workers: {self.completed_workers}/{self.total_workers} completed, {self.active_workers} active, {self.failed_workers} failed",
        ]
        if self.spent_cost > 0.0:
            lines.append(f"Cost: ${self.spent_cost:.4f} spent (est ${self.cost_estimate:.4f})")
        if self.warnings:
            lines.append(f"Warnings: {len(self.warnings)} ({self.warnings[0]})")
        return " | ".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "swarm_id": self.swarm_id,
            "status": self.status.value,
            "strategy": self.strategy.value,
            "total_workers": self.total_workers,
            "active_workers": self.active_workers,
            "queued_workers": self.queued_workers,
            "completed_workers": self.completed_workers,
            "failed_workers": self.failed_workers,
            "current_phase": self.current_phase,
            "estimated_progress": self.estimated_progress,
            "resource_usage": self.resource_usage,
            "cost_estimate": self.cost_estimate,
            "spent_cost": self.spent_cost,
            "warnings": list(self.warnings),
            "summary": self.summary_text(),
        }
