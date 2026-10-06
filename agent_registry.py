"""Agent Registry for Hey Jev Orchestrator.

Manages metadata, capabilities, permissions, health, and adapter instances
for all available agent providers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agent_adapter import AgentAdapter
from agent_task import TaskPermission


@dataclass
class AgentMetadata:
    """Metadata describing an agent's capabilities, costs, and constraints."""
    name: str
    provider: str
    capabilities: List[str] = field(default_factory=list)
    supported_tasks: List[str] = field(default_factory=list)
    cost: str = "medium"  # "free", "low", "medium", "high"
    speed: str = "medium"  # "fast", "medium", "slow"
    reliability: float = 0.90  # 0.0 to 1.0
    availability: str = "available"  # "available", "degraded", "offline"
    permissions: List[TaskPermission] = field(default_factory=list)
    auth_requirements: List[str] = field(default_factory=list)
    supports_streaming: bool = True
    supports_long_running: bool = True
    supports_background: bool = True
    health_status: str = "healthy"  # "healthy", "unhealthy", "unknown"
    adapter_class: Optional[str] = None

    def __post_init__(self) -> None:
        normalized: List[TaskPermission] = []
        for p in self.permissions:
            if isinstance(p, str):
                try:
                    normalized.append(TaskPermission(p))
                except ValueError:
                    pass
            elif isinstance(p, TaskPermission):
                normalized.append(p)
        self.permissions = normalized

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to plain dictionary."""
        return {
            "name": self.name,
            "provider": self.provider,
            "capabilities": list(self.capabilities),
            "supported_tasks": list(self.supported_tasks),
            "cost": self.cost,
            "speed": self.speed,
            "reliability": self.reliability,
            "availability": self.availability,
            "permissions": [p.value for p in self.permissions],
            "auth_requirements": list(self.auth_requirements),
            "supports_streaming": self.supports_streaming,
            "supports_long_running": self.supports_long_running,
            "supports_background": self.supports_background,
            "health_status": self.health_status,
            "adapter_class": self.adapter_class,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentMetadata:
        """Reconstruct metadata from dictionary."""
        d = dict(data)
        if "permissions" in d:
            d["permissions"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permissions"]
            ]
        return cls(**d)


class AgentRegistry:
    """Registry maintaining active and known agent providers."""

    def __init__(self, seed_defaults: bool = True) -> None:
        self._agents: Dict[str, AgentMetadata] = {}
        self._adapters: Dict[str, AgentAdapter] = {}

        if seed_defaults:
            self._seed_default_agents()

    def _seed_default_agents(self) -> None:
        """Seed registry with standard agent definitions."""
        self.register_agent(
            AgentMetadata(
                name="antigravity",
                provider="google",
                capabilities=[
                    "coding",
                    "debugging",
                    "repository_editing",
                    "command_execution",
                    "file_inspection",
                ],
                supported_tasks=["code_generation", "bug_fix", "refactor", "diagnostics"],
                cost="medium",
                speed="medium",
                reliability=0.96,
                availability="available",
                permissions=[
                    TaskPermission.READ_FILES,
                    TaskPermission.WRITE_FILES,
                    TaskPermission.EXECUTE_COMMAND,
                ],
                adapter_class="AntigravityAdapter",
            )
        )

        self.register_agent(
            AgentMetadata(
                name="tier3_pc",
                provider="hey_jev",
                capabilities=[
                    "pc_control",
                    "windows_gui",
                    "hotkeys",
                    "system_settings",
                    "mcp_tools",
                ],
                supported_tasks=["window_control", "application_launch", "system_volume"],
                cost="low",
                speed="fast",
                reliability=0.92,
                availability="available",
                permissions=[
                    TaskPermission.CHANGE_SETTINGS,
                    TaskPermission.EXECUTE_COMMAND,
                ],
                adapter_class="Tier3AgentAdapter",
            )
        )

        self.register_agent(
            AgentMetadata(
                name="claude",
                provider="anthropic",
                capabilities=["analysis", "review", "reasoning", "evaluation"],
                supported_tasks=["code_review", "architecture_planning", "summarization"],
                cost="medium",
                speed="medium",
                reliability=0.98,
                availability="available",
                permissions=[],
                adapter_class=None,
            )
        )

        self.register_agent(
            AgentMetadata(
                name="research_agent",
                provider="web_search",
                capabilities=["research", "web_investigation", "synthesis"],
                supported_tasks=["hotel_search", "documentation_lookup", "web_query"],
                cost="low",
                speed="medium",
                reliability=0.88,
                availability="available",
                permissions=[TaskPermission.NETWORK_ACCESS],
                adapter_class=None,
            )
        )

        self.register_agent(
            AgentMetadata(
                name="windows_agent",
                provider="system",
                capabilities=[
                    "computer_use",
                    "windows_ui",
                    "filesystem",
                    "application_control",
                    "window_management",
                    "settings",
                    "wallpaper",
                    "powershell",
                    "observation",
                    "verification",
                    "dry_run",
                    "file_organize",
                ],
                supported_tasks=[
                    "open_picture",
                    "change_wallpaper",
                    "dark_mode",
                    "organize_downloads",
                    "window_control",
                    "application_lifecycle",
                    "powershell_execution",
                    "folder_cleanup",
                ],
                cost="free",
                speed="fast",
                reliability=0.97,
                availability="available",
                permissions=[
                    TaskPermission.READ_FILES,
                    TaskPermission.WRITE_FILES,
                    TaskPermission.CHANGE_SETTINGS,
                    TaskPermission.EXECUTE_COMMAND,
                ],
                adapter_class="WindowsComputerAdapter",
            )
        )
        try:
            from windows_computer_adapter import WindowsComputerAdapter
            self.bind_adapter("windows_agent", WindowsComputerAdapter())
        except Exception:
            pass

    def register_agent(self, metadata: AgentMetadata, adapter: Optional[AgentAdapter] = None) -> None:
        """Register an agent metadata record and optional adapter instance."""
        self._agents[metadata.name] = metadata
        if adapter is not None:
            self._adapters[metadata.name] = adapter

    def unregister_agent(self, name: str) -> bool:
        """Remove an agent from the registry."""
        found = name in self._agents
        self._agents.pop(name, None)
        self._adapters.pop(name, None)
        return found

    def get_agent(self, name: str) -> Optional[AgentMetadata]:
        """Lookup agent metadata by name."""
        return self._agents.get(name)

    def get_adapter(self, name: str) -> Optional[AgentAdapter]:
        """Lookup active adapter by name."""
        return self._adapters.get(name)

    def bind_adapter(self, name: str, adapter: AgentAdapter) -> None:
        """Bind an active adapter instance to a registered agent."""
        if name in self._agents:
            self._adapters[name] = adapter

    def list_agents(
        self,
        filter_fn: Optional[Callable[[AgentMetadata], bool]] = None,
    ) -> List[AgentMetadata]:
        """List registered agents, optionally filtered."""
        if filter_fn is None:
            return list(self._agents.values())
        return [a for a in self._agents.values() if filter_fn(a)]

    def find_by_capability(self, capability: str) -> List[AgentMetadata]:
        """Find all agents offering a specific capability."""
        cap_lower = capability.lower()
        return [
            a for a in self._agents.values()
            if any(cap_lower in c.lower() for c in a.capabilities)
        ]

    def update_health(
        self,
        name: str,
        health_status: str,
        availability: Optional[str] = None,
    ) -> bool:
        """Update health and availability status for an agent."""
        agent = self.get_agent(name)
        if not agent:
            return False
        agent.health_status = health_status
        if availability:
            agent.availability = availability
        return True
