"""Unified Capability Model for Hey Jev (Phase 22).

Provides a common, provider-neutral capability schema unifying primitive tools
(Phase 15 ToolDefinition) and composite workflows (Phase 15 SkillDefinition)
into outcome-oriented capability descriptors.

Each capability specifies:
- Stable identifier and human-readable name
- Purpose and supported outcomes
- Input schema, required arguments, preconditions
- Expected output schema and observable success criteria
- Risk category, approval requirement, reversibility (READ_ONLY, REVERSIBLE, IRREVERSIBLE)
- Required permissions and resource constraints
- Timeout, failure handling, verification strategy
- Operational health, version, provenance, and execution handler
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Union

from agent_task import TaskPermission
from skill_model import SkillDefinition, SkillTrustLevel
from tool_model import RiskLevel, ToolDefinition, ToolHealth


class CapabilityType(str, Enum):
    """Architectural classification of a capability."""
    TOOL = "TOOL"              # Atomic single-operation tool
    SKILL = "SKILL"            # Composed multi-step declarative workflow
    COMPOSITE = "COMPOSITE"    # Dynamically generated capability


class Reversibility(str, Enum):
    """Side-effect impact and undo support."""
    READ_ONLY = "READ_ONLY"        # Zero disk/system mutations
    REVERSIBLE = "REVERSIBLE"      # Modifies state but supports automated undo/rollback
    IRREVERSIBLE = "IRREVERSIBLE"  # Permanent destructive modification (e.g. permanent deletion, shell exec)


class ApprovalRequirement(str, Enum):
    """Gate policy for human interaction before execution."""
    NONE = "NONE"                  # Safe to execute under normal policy
    RECOMMENDED = "RECOMMENDED"    # Low impact but user confirmation recommended
    MANDATORY = "MANDATORY"        # Execution strictly blocked until human confirms


@dataclass
class CapabilityDefinition:
    """Formal, unified outcome-oriented descriptor for tools and skills."""
    capability_id: str
    name: str
    description: str
    supported_outcomes: List[str] = field(default_factory=list)
    capability_type: CapabilityType = CapabilityType.TOOL
    input_schema: Dict[str, Any] = field(default_factory=dict)
    required_arguments: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    expected_output: Dict[str, Any] = field(default_factory=dict)
    observable_success_criteria: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    approval_requirement: ApprovalRequirement = ApprovalRequirement.NONE
    reversibility: Reversibility = Reversibility.READ_ONLY
    permissions: List[TaskPermission] = field(default_factory=list)
    resource_constraints: Dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 30.0
    failure_handling: str = "abort"  # "retry", "abort", "fallback"
    verification_strategy: str = "output_non_empty"
    health: ToolHealth = ToolHealth.HEALTHY
    version: str = "1.0.0"
    provenance: str = "builtin"
    underlying_id: str = ""
    supports_dry_run: bool = False
    supports_rollback: bool = False
    handler: Optional[Callable] = None
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if isinstance(self.capability_type, str):
            try:
                self.capability_type = CapabilityType(self.capability_type.upper())
            except ValueError:
                self.capability_type = CapabilityType.TOOL
        if isinstance(self.risk_level, str):
            try:
                self.risk_level = RiskLevel(self.risk_level.upper())
            except ValueError:
                self.risk_level = RiskLevel.LOW
        if isinstance(self.approval_requirement, str):
            try:
                self.approval_requirement = ApprovalRequirement(self.approval_requirement.upper())
            except ValueError:
                self.approval_requirement = ApprovalRequirement.NONE
        if isinstance(self.reversibility, str):
            try:
                self.reversibility = Reversibility(self.reversibility.upper())
            except ValueError:
                self.reversibility = Reversibility.READ_ONLY
        if isinstance(self.health, str):
            try:
                self.health = ToolHealth(self.health.upper())
            except ValueError:
                self.health = ToolHealth.HEALTHY

        # Auto-compute approval requirement from risk if not specified
        if self.approval_requirement == ApprovalRequirement.NONE:
            if self.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                self.approval_requirement = ApprovalRequirement.MANDATORY
            elif self.risk_level == RiskLevel.MEDIUM and self.reversibility == Reversibility.IRREVERSIBLE:
                self.approval_requirement = ApprovalRequirement.MANDATORY

        # Normalize permissions
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

        # Ensure underlying_id defaults to capability_id if empty
        if not self.underlying_id:
            self.underlying_id = self.capability_id

    @property
    def is_available(self) -> bool:
        """Return True if capability is healthy or usable."""
        return self.health in (ToolHealth.HEALTHY, ToolHealth.DEGRADED)

    @property
    def is_read_only(self) -> bool:
        return self.reversibility == Reversibility.READ_ONLY

    def to_dict(self) -> Dict[str, Any]:
        """Serialize definition to a JSON-compatible dictionary."""
        d = {
            "capability_id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "supported_outcomes": list(self.supported_outcomes),
            "capability_type": self.capability_type.value,
            "input_schema": self.input_schema,
            "required_arguments": list(self.required_arguments),
            "preconditions": list(self.preconditions),
            "expected_output": self.expected_output,
            "observable_success_criteria": list(self.observable_success_criteria),
            "risk_level": self.risk_level.value,
            "approval_requirement": self.approval_requirement.value,
            "reversibility": self.reversibility.value,
            "permissions": [p.value for p in self.permissions],
            "resource_constraints": self.resource_constraints,
            "timeout_s": self.timeout_s,
            "failure_handling": self.failure_handling,
            "verification_strategy": self.verification_strategy,
            "health": self.health.value,
            "version": self.version,
            "provenance": self.provenance,
            "underlying_id": self.underlying_id,
            "supports_dry_run": self.supports_dry_run,
            "supports_rollback": self.supports_rollback,
            "tags": list(self.tags),
            "created_at": self.created_at,
        }
        return d

    @classmethod
    def from_tool_definition(
        cls,
        tool: ToolDefinition,
        handler: Optional[Callable] = None,
    ) -> CapabilityDefinition:
        """Construct a CapabilityDefinition from a Phase 15 ToolDefinition."""
        # Infer required arguments from input_schema
        req_args = tool.input_schema.get("required", []) if isinstance(tool.input_schema, dict) else []

        # Infer reversibility and outcomes
        reversibility = Reversibility.READ_ONLY
        supported_outcomes = [f"execute_{tool.tool_id}"]
        for cap in tool.capabilities:
            supported_outcomes.append(f"perform_{cap}")

        if tool.risk_level == RiskLevel.LOW and not tool.supports_rollback:
            reversibility = Reversibility.READ_ONLY
        elif tool.supports_rollback:
            reversibility = Reversibility.REVERSIBLE
        elif tool.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            reversibility = Reversibility.IRREVERSIBLE
        elif tool.category in ("filesystem", "desktop") and "write" in str(tool.permissions).lower():
            reversibility = Reversibility.REVERSIBLE if tool.supports_rollback else Reversibility.IRREVERSIBLE

        # Success criteria
        criteria = ["output_present"]
        if tool.category == "filesystem" and "list" in tool.tool_id:
            criteria = ["entries_is_list", "count_matches_entries"]
            supported_outcomes.extend(["list_files", "discover_files", "find_pictures"])
        elif "move" in tool.tool_id:
            criteria = ["file_moved_to_destination", "source_file_absent"]
            supported_outcomes.extend(["organize_files", "relocate_items"])
        elif "wallpaper" in tool.tool_id:
            criteria = ["wallpaper_path_set_in_registry"]
            supported_outcomes.extend(["change_desktop_background", "set_wallpaper_image"])
        elif "browser" in tool.tool_id:
            criteria = ["browser_session_active"]
            supported_outcomes.extend(["browse_web_pages", "extract_online_data"])

        approval = ApprovalRequirement.MANDATORY if tool.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) else ApprovalRequirement.NONE

        return cls(
            capability_id=tool.tool_id,
            name=tool.name,
            description=tool.description,
            supported_outcomes=supported_outcomes,
            capability_type=CapabilityType.TOOL,
            input_schema=tool.input_schema,
            required_arguments=list(req_args),
            expected_output=tool.output_schema,
            observable_success_criteria=criteria,
            risk_level=tool.risk_level,
            approval_requirement=approval,
            reversibility=reversibility,
            permissions=list(tool.permissions),
            timeout_s=30.0 if tool.latency_class == "slow" else 10.0,
            verification_strategy="evidence_based" if tool.supports_verification else "output_non_empty",
            health=tool.health,
            version=tool.version,
            provenance=f"tool_registry:{tool.provider}",
            underlying_id=tool.tool_id,
            supports_dry_run=tool.supports_dry_run,
            supports_rollback=tool.supports_rollback,
            handler=handler or tool.handler_reference,
            tags=list(tool.capabilities) + [tool.category],
        )

    @classmethod
    def from_skill_definition(
        cls,
        skill: SkillDefinition,
        executor: Optional[Any] = None,
    ) -> CapabilityDefinition:
        """Construct a CapabilityDefinition from a Phase 15 SkillDefinition."""
        req_args = [k for k, v in skill.inputs.items() if isinstance(v, dict) and v.get("required", False)]
        if not req_args and skill.inputs:
            req_args = list(skill.inputs.keys())

        # Skill outcomes
        supported_outcomes = [f"accomplish_{skill.skill_id}", skill.name.lower().replace(" ", "_")]
        for tag in skill.tags:
            supported_outcomes.append(f"perform_{tag}")

        reversibility = Reversibility.REVERSIBLE if skill.supports_dry_run else (
            Reversibility.READ_ONLY if skill.risk_level == RiskLevel.LOW else Reversibility.IRREVERSIBLE
        )

        approval = ApprovalRequirement.MANDATORY if (
            skill.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) or skill.trust_level == SkillTrustLevel.UNTRUSTED
        ) else (
            ApprovalRequirement.RECOMMENDED if skill.risk_level == RiskLevel.MEDIUM else ApprovalRequirement.NONE
        )

        criteria = [skill.verification_strategy]

        handler = None
        if executor and hasattr(executor, "execute_skill"):
            handler = lambda **kwargs: executor.execute_skill(skill, kwargs)

        return cls(
            capability_id=f"skill:{skill.skill_id}",
            name=skill.name,
            description=skill.description,
            supported_outcomes=supported_outcomes,
            capability_type=CapabilityType.SKILL,
            input_schema={"type": "object", "properties": skill.inputs},
            required_arguments=req_args,
            preconditions=[f"tools_available:{','.join(skill.required_tools)}"],
            expected_output={"type": "object", "properties": skill.outputs},
            observable_success_criteria=criteria,
            risk_level=skill.risk_level,
            approval_requirement=approval,
            reversibility=reversibility,
            permissions=list(skill.permissions),
            timeout_s=60.0,
            failure_handling="abort",
            verification_strategy=skill.verification_strategy,
            health=ToolHealth.HEALTHY,
            version=skill.version,
            provenance=f"skill_registry:{skill.author}",
            underlying_id=skill.skill_id,
            supports_dry_run=skill.supports_dry_run,
            supports_rollback=False,
            handler=handler,
            tags=list(skill.tags) + [skill.category],
        )
