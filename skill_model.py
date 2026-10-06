"""Skill Model, Trust Tiers, and Workflow Step Specifications for Hey Jev.

Provides:
- SkillTrustLevel enum (CORE, VERIFIED, COMMUNITY, EXPERIMENTAL, UNTRUSTED)
- SkillStep for declarative DAG / conditional workflow compositions
- SkillDefinition formal schema with versioning and capability requirements
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from agent_task import TaskPermission
from tool_model import RiskLevel


class SkillTrustLevel(str, Enum):
    """Trust classification for skills governing permission bounds."""
    CORE = "CORE"                  # Built-in to Hey Jev (full trust)
    VERIFIED = "VERIFIED"          # Verified internal / approved extensions
    COMMUNITY = "COMMUNITY"        # Community submitted, verified manifest
    EXPERIMENTAL = "EXPERIMENTAL"  # Experimental, constrained execution
    UNTRUSTED = "UNTRUSTED"        # Untrusted, never receives privileged permissions


@dataclass
class SkillStep:
    """Atomic step within a skill's composed workflow."""
    step_id: str
    tool_id: str
    description: str = ""
    inputs: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Dict[str, Any]] = None  # e.g., {"field": "files_found", "op": ">", "val": 0}
    depends_on: List[str] = field(default_factory=list)
    retry_count: int = 0
    verify: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillStep:
        return cls(**data)


@dataclass
class SkillDefinition:
    """Reusable multi-tool capability or workflow specification."""
    skill_id: str
    name: str
    description: str
    version: str = "1.0.0"
    category: str = "general"
    required_capabilities: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)
    permissions: List[TaskPermission] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    workflow: List[Dict[str, Any]] = field(default_factory=list)
    verification_strategy: str = "output_non_empty"
    supports_dry_run: bool = False
    supports_background: bool = True
    tags: List[str] = field(default_factory=list)
    author: str = "hey_jev"
    source: str = "builtin"
    trust_level: SkillTrustLevel = SkillTrustLevel.CORE
    license: str = "MIT"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.risk_level, str):
            try:
                self.risk_level = RiskLevel(self.risk_level.upper())
            except ValueError:
                self.risk_level = RiskLevel.LOW
        if isinstance(self.trust_level, str):
            try:
                self.trust_level = SkillTrustLevel(self.trust_level.upper())
            except ValueError:
                self.trust_level = SkillTrustLevel.CORE

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

    @property
    def versioned_id(self) -> str:
        """Version-pinned identifier (e.g. downloads.organization@1.0)."""
        return f"{self.skill_id}@{self.version}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "versioned_id": self.versioned_id,
            "category": self.category,
            "required_capabilities": list(self.required_capabilities),
            "required_tools": list(self.required_tools),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "permissions": [p.value for p in self.permissions],
            "risk_level": self.risk_level.value,
            "workflow": self.workflow,
            "verification_strategy": self.verification_strategy,
            "supports_dry_run": self.supports_dry_run,
            "supports_background": self.supports_background,
            "tags": list(self.tags),
            "author": self.author,
            "source": self.source,
            "trust_level": self.trust_level.value,
            "license": self.license,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillDefinition:
        d = dict(data)
        d.pop("versioned_id", None)
        if "permissions" in d:
            d["permissions"] = [
                TaskPermission(p) if isinstance(p, str) else p
                for p in d["permissions"]
            ]
        if "risk_level" in d and isinstance(d["risk_level"], str):
            d["risk_level"] = RiskLevel(d["risk_level"])
        if "trust_level" in d and isinstance(d["trust_level"], str):
            d["trust_level"] = SkillTrustLevel(d["trust_level"])
        return cls(**d)
