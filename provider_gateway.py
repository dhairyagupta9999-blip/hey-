"""Provider Gateway and Model Routing for Hey Jev.

Central entry point for all external AI models and federated agents.
Handles:
- Provider discovery, health telemetry, and capability catalog
- Provider routing (ModelRouter) with cost modes (FREE_FIRST, BALANCED, etc.)
- Strict budget guarding (zero-cost enforcement)
- Rate limit mitigation with exponential backoff & provider fallback
- Secret scrubbing and context partitioning
- Health history tracking and provider operational learning
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from agent_result import AgentResult, AgentResultStatus
from provider_adapters import (
    A2ATransportAdapter,
    AntigravityProviderAdapter,
    AnthropicCompatibleAdapter,
    BrowserFallbackTransport,
    LocalModelAdapter,
    OpenAICompatibleAdapter,
    ProviderTransport,
)
from swarm_model import CostMode, SwarmBudget


class ProviderType(str, Enum):
    """Recognized provider architectures."""
    ANTIGRAVITY = "ANTIGRAVITY"
    OPENAI = "OPENAI"
    ANTHROPIC = "ANTHROPIC"
    CODEX = "CODEX"
    LOCAL_MODEL = "LOCAL_MODEL"
    A2A_REMOTE = "A2A_REMOTE"
    BROWSER_FALLBACK = "BROWSER_FALLBACK"
    CUSTOM = "CUSTOM"


@dataclass
class ProviderHealthRecord:
    """Historical telemetry tracking a provider's reliability and latency."""
    provider_name: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rate_limited_calls: int = 0
    timeout_calls: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    last_status: str = "healthy"
    last_error: Optional[str] = None
    last_checked_at: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.successful_calls / float(self.total_calls)

    @property
    def avg_latency_ms(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / float(self.total_calls)

    def record_call(
        self,
        success: bool,
        latency_ms: float,
        cost: float = 0.0,
        rate_limited: bool = False,
        timeout: bool = False,
        error: Optional[str] = None,
    ) -> None:
        self.total_calls += 1
        self.total_latency_ms += latency_ms
        self.total_cost_usd += cost
        self.last_checked_at = time.time()
        if success:
            self.successful_calls += 1
            self.last_status = "healthy"
            self.last_error = None
        else:
            self.failed_calls += 1
            self.last_error = error
            if rate_limited:
                self.rate_limited_calls += 1
                self.last_status = "rate_limited"
            elif timeout:
                self.timeout_calls += 1
                self.last_status = "timeout"
            else:
                self.last_status = "failed"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ModelRouter:
    """Evaluates provider capability, health history, cost modes, and permissions."""

    def __init__(self, cost_mode: CostMode = CostMode.FREE_FIRST) -> None:
        self.cost_mode = cost_mode

    def score_provider(
        self,
        provider_name: str,
        transport: ProviderTransport,
        health: ProviderHealthRecord,
        required_capabilities: List[str],
        budget: Optional[SwarmBudget] = None,
        preferred_provider: Optional[str] = None,
    ) -> float:
        """Score provider fitness (0.0 to 100.0). Higher is better."""
        # Check capability match
        caps = transport.capabilities()
        match_count = sum(1 for c in required_capabilities if c in caps)
        if required_capabilities and match_count == 0:
            return 0.0  # Incompatible

        score = 50.0 + (match_count * 10.0)

        # Factor in historical success rate
        score += (health.success_rate * 25.0)

        # Penalize degraded / rate-limited status
        if health.last_status == "rate_limited":
            score -= 40.0
        elif health.last_status in ("failed", "timeout"):
            score -= 30.0

        # Cost policy evaluations
        is_free = (provider_name in ("antigravity", "local_model") or "mock" in provider_name)
        if self.cost_mode in (CostMode.FREE_FIRST, CostMode.LOCAL_ONLY, CostMode.OFFLINE_ONLY):
            if is_free:
                score += 30.0
            else:
                score -= 40.0
        elif self.cost_mode == CostMode.QUALITY_FIRST:
            if provider_name in ("anthropic", "antigravity", "mock_high_quality"):
                score += 20.0

        # Budget enforcement
        if budget and budget.max_cost == 0.0 and not is_free:
            return 0.0  # Zero budget strictly forbids paid providers

        if preferred_provider and provider_name == preferred_provider:
            score += 25.0

        return max(0.0, score)


class ProviderGateway:
    """Central gateway providing unified access, routing, and safety to AI providers."""

    SECRET_PATTERNS = [
        re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),
        re.compile(r"ghp_[a-zA-Z0-9]{36}"),
        re.compile(r"AIza[0-9A-Za-z-_]{35}"),
        re.compile(r"(?:bearer\s+)[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
    ]

    def __init__(
        self,
        default_cost_mode: CostMode = CostMode.FREE_FIRST,
        seed_defaults: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self.default_cost_mode = default_cost_mode
        self.router = ModelRouter(cost_mode=default_cost_mode)
        self._transports: Dict[str, ProviderTransport] = {}
        self._health_history: Dict[str, ProviderHealthRecord] = {}
        self._operational_lessons: List[Dict[str, Any]] = []

        if seed_defaults:
            self._seed_default_transports()

    def _seed_default_transports(self) -> None:
        """Register built-in transports."""
        self.register_transport("antigravity", AntigravityProviderAdapter())
        self.register_transport("openai_compatible", OpenAICompatibleAdapter())
        self.register_transport("anthropic", AnthropicCompatibleAdapter())
        self.register_transport("local_model", LocalModelAdapter())
        self.register_transport("a2a_remote", A2ATransportAdapter())
        self.register_transport("browser_fallback", BrowserFallbackTransport())

    def register_transport(self, name: str, transport: ProviderTransport) -> None:
        with self._lock:
            self._transports[name] = transport
            if name not in self._health_history:
                self._health_history[name] = ProviderHealthRecord(provider_name=name)

    def get_transport(self, name: str) -> Optional[ProviderTransport]:
        with self._lock:
            return self._transports.get(name)

    def scrub_secrets(self, text: str) -> str:
        """Scrub known API keys and tokens before sending externally."""
        scrubbed = text
        for pat in self.SECRET_PATTERNS:
            scrubbed = pat.sub("[REDACTED_SECRET]", scrubbed)
        return scrubbed

    def partition_context(self, context: Dict[str, Any], role: str) -> Dict[str, Any]:
        """Apply need-to-know context isolation based on specialist role."""
        partitioned: Dict[str, Any] = {}
        # Scrub all string fields
        for k, v in context.items():
            if isinstance(v, str):
                v = self.scrub_secrets(v)
            partitioned[k] = v

        # Role-specific filtering
        if role == "RESEARCHER":
            allowed = {"goal", "query", "keywords", "target_url", "prior_evidence"}
            return {k: v for k, v in partitioned.items() if k in allowed}
        elif role in ("CODER", "TESTER"):
            allowed = {"goal", "repo_path", "test_file", "error", "traceback", "diff"}
            return {k: v for k, v in partitioned.items() if k in allowed}
        elif role == "REVIEWER":
            allowed = {"goal", "diff", "requirements", "criteria", "test_output"}
            return {k: v for k, v in partitioned.items() if k in allowed}

        return partitioned

    def select_best_provider(
        self,
        required_capabilities: List[str],
        budget: Optional[SwarmBudget] = None,
        cost_mode: Optional[CostMode] = None,
        preferred_provider: Optional[str] = None,
        fallback_exclude: Optional[Set[str]] = None,
    ) -> Tuple[Optional[str], Optional[ProviderTransport]]:
        """Determine highest-scoring available provider."""
        with self._lock:
            mode = cost_mode or self.default_cost_mode
            self.router.cost_mode = mode
            excluded = fallback_exclude or set()

            scored: List[Tuple[float, str, ProviderTransport]] = []
            for name, transport in self._transports.items():
                if name in excluded:
                    continue
                health = self._health_history.get(name, ProviderHealthRecord(provider_name=name))
                score = self.router.score_provider(
                    provider_name=name,
                    transport=transport,
                    health=health,
                    required_capabilities=required_capabilities,
                    budget=budget,
                    preferred_provider=preferred_provider,
                )
                if score > 0.0:
                    scored.append((score, name, transport))

            if not scored:
                return None, None

            scored.sort(key=lambda s: -s[0])
            best_score, best_name, best_transport = scored[0]
            return best_name, best_transport

    def submit_task(
        self,
        task_id: str,
        prompt: str,
        role: str = "WORKER",
        provider_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        budget: Optional[SwarmBudget] = None,
        cost_mode: Optional[CostMode] = None,
        max_retries: int = 2,
        allow_fallback: bool = True,
        **kwargs,
    ) -> Tuple[bool, str, Optional[AgentResult]]:
        """Submit task with automatic secret scrubbing, budget guarding, and rate-limit fallback."""
        scrubbed_prompt = self.scrub_secrets(prompt)
        safe_ctx = self.partition_context(context or {}, role)

        # Budget check
        estimated_cost = 0.0
        target_name = provider_name
        transport = self.get_transport(target_name) if target_name else None

        if not transport:
            target_name, transport = self.select_best_provider(
                required_capabilities=[],
                budget=budget,
                cost_mode=cost_mode,
            )

        if not transport or not target_name:
            return False, "no_provider_available", AgentResult(
                error="No compatible provider available under current budget and constraints",
                status=AgentResultStatus.BLOCKED,
                confidence=0.0,
            )

        # Enforce zero-budget policy
        if budget and budget.max_cost == 0.0:
            est = transport.cost_estimate(scrubbed_prompt)
            if est > 0.0 and target_name not in ("antigravity", "local_model"):
                return False, "budget_exceeded", AgentResult(
                    error="Zero cost policy strictly forbids paid API usage",
                    status=AgentResultStatus.BLOCKED,
                    confidence=0.0,
                )

        excluded: Set[str] = set()
        attempts = 0

        while attempts <= max_retries:
            attempts += 1
            t_start = time.time()
            try:
                accepted = transport.submit(task_id, scrubbed_prompt, safe_ctx, **kwargs)
                latency_ms = (time.time() - t_start) * 1000.0

                if accepted:
                    status = transport.get_status(task_id)
                    if status == "RATE_LIMITED":
                        self._record_telemetry(target_name, success=False, latency_ms=latency_ms, rate_limited=True)
                        if not allow_fallback:
                            return False, target_name, transport.get_result(task_id)
                        excluded.add(target_name)
                        # Attempt fallback
                        target_name, transport = self.select_best_provider([], budget, cost_mode, fallback_exclude=excluded)
                        if not transport:
                            break
                        continue

                    # Retrieve result
                    res = transport.get_result(task_id)
                    if res and not res.is_success:
                        self._record_telemetry(target_name, success=False, latency_ms=latency_ms, error=res.error)
                        if not allow_fallback:
                            return False, target_name, res
                        excluded.add(target_name)
                        target_name, transport = self.select_best_provider([], budget, cost_mode, fallback_exclude=excluded)
                        if not transport:
                            break
                        continue

                    self._record_telemetry(target_name, success=True, latency_ms=latency_ms)
                    return True, target_name, res
                else:
                    self._record_telemetry(target_name, success=False, latency_ms=latency_ms)
                    if not allow_fallback:
                        res = transport.get_result(task_id) or AgentResult(error="Provider rejected task", status=AgentResultStatus.FAILURE)
                        return False, target_name, res
                    excluded.add(target_name)
                    target_name, transport = self.select_best_provider([], budget, cost_mode, fallback_exclude=excluded)
                    if not transport:
                        break
            except Exception as e:
                latency_ms = (time.time() - t_start) * 1000.0
                self._record_telemetry(target_name, success=False, latency_ms=latency_ms, error=str(e))
                if not allow_fallback:
                    return False, target_name, AgentResult(error=str(e), status=AgentResultStatus.FAILURE)
                excluded.add(target_name)
                target_name, transport = self.select_best_provider([], budget, cost_mode, fallback_exclude=excluded)
                if not transport:
                    break

        return False, target_name or "unknown", AgentResult(
            error="Task execution failed across primary and fallback providers",
            status=AgentResultStatus.FAILURE,
            confidence=0.0,
        )

    def _record_telemetry(
        self,
        provider_name: str,
        success: bool,
        latency_ms: float,
        cost: float = 0.0,
        rate_limited: bool = False,
        timeout: bool = False,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            rec = self._health_history.get(provider_name)
            if rec:
                rec.record_call(success, latency_ms, cost, rate_limited, timeout, error)

    def record_learning_lesson(self, provider_name: str, domain: str, lesson: str, rating: float) -> None:
        """Store operational insights regarding provider performance in specific domains."""
        with self._lock:
            self._operational_lessons.append({
                "timestamp": time.time(),
                "provider": provider_name,
                "domain": domain,
                "lesson": lesson,
                "rating": rating,
            })

    def get_health_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._health_history.items()}

    def discover_all(self) -> Dict[str, Any]:
        with self._lock:
            return {
                name: transport.discover()
                for name, transport in self._transports.items()
            }
