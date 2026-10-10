"""Extension Build Plan Model and Validation for Hey Jev (Phase 24).

Defines structured build specifications linking an approved or validated
extension proposal to a bounded, reviewable implementation plan.

Governing Invariant:
Building code authorizes construction within an isolated workspace;
it does NOT silently grant permission to install, execute, or activate it.
"""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from extension_proposal import ExtensionProposal, ProposalStatus


@dataclass
class BuildResourceLimits:
    """Resource constraints for extension building and testing."""
    max_file_size_bytes: int = 500_000          # 500 KB per file
    max_total_workspace_bytes: int = 5_000_000   # 5 MB total workspace
    max_execution_time_seconds: float = 5.0      # 5s max execution time
    max_memory_mb: int = 256                     # 256 MB memory limit
    max_retries: int = 1                         # Non-idempotent builds not retried automatically

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BuildFileSpec:
    """Specification of an authorized file permitted to be generated in workspace."""
    relative_path: str
    purpose: str = "implementation"  # "implementation", "test", "metadata", "docs"
    is_executable: bool = False
    expected_content_hash: str = ""

    def __post_init__(self) -> None:
        # Normalize and strip any leading slashes or backslashes
        norm = os.path.normpath(self.relative_path.replace("\\", "/"))
        if norm.startswith((".", "/", "\\")):
            norm = norm.lstrip("./\\")
        self.relative_path = norm

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BuildCommand:
    """Explicitly authorized build or test command."""
    command: str
    args: List[str] = field(default_factory=list)
    purpose: str = "unit_test"  # "static_check", "unit_test", "smoke_test"
    timeout_seconds: float = 5.0
    requires_network: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtensionBuildPlan:
    """Formal construction specification for an extension proposal."""
    plan_id: str
    proposal_id: str
    candidate_hash: str
    candidate_id: str
    candidate_version: str
    functional_requirements: List[str] = field(default_factory=list)
    input_contract: Dict[str, Any] = field(default_factory=dict)
    output_contract: Dict[str, Any] = field(default_factory=dict)
    permitted_files: List[BuildFileSpec] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    permitted_commands: List[BuildCommand] = field(default_factory=list)
    required_permissions: List[TaskPermission] = field(default_factory=list)
    test_criteria: List[str] = field(default_factory=list)
    resource_limits: BuildResourceLimits = field(default_factory=BuildResourceLimits)
    rollback_strategy: str = "Unregister capability and purge workspace files"
    created_at: float = field(default_factory=time.time)

    @classmethod
    def from_proposal(
        cls,
        proposal: ExtensionProposal,
        functional_requirements: Optional[List[str]] = None,
        permitted_relative_paths: Optional[List[str]] = None,
        resource_limits: Optional[BuildResourceLimits] = None,
    ) -> ExtensionBuildPlan:
        """Construct a validated build plan bound to a proposal's immutable candidate hash."""
        cand = proposal.candidate
        cand_slug = re.sub(r"[^a-zA-Z0-9_]", "_", cand.candidate_id.split(":")[-1])

        default_files = [
            BuildFileSpec(relative_path=f"{cand_slug}.py", purpose="implementation"),
            BuildFileSpec(relative_path=f"test_{cand_slug}.py", purpose="test"),
            BuildFileSpec(relative_path="manifest.json", purpose="metadata"),
        ]
        if permitted_relative_paths:
            file_specs = [BuildFileSpec(relative_path=p) for p in permitted_relative_paths]
        else:
            file_specs = default_files

        reqs = list(functional_requirements or cand.provided_capabilities or [cand.description])

        plan = cls(
            plan_id=f"build_plan_{uuid.uuid4().hex[:10]}",
            proposal_id=proposal.proposal_id,
            candidate_hash=proposal.candidate_hash,
            candidate_id=cand.candidate_id,
            candidate_version=cand.version,
            functional_requirements=reqs,
            permitted_files=file_specs,
            dependencies=list(cand.dependencies),
            required_permissions=list(proposal.requested_permissions),
            resource_limits=resource_limits or BuildResourceLimits(),
            test_criteria=[
                "normal_operation_passes",
                "malformed_input_rejected",
                "execution_within_timeout",
                "declared_permissions_honored",
            ],
        )
        return plan

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "proposal_id": self.proposal_id,
            "candidate_hash": self.candidate_hash,
            "candidate_id": self.candidate_id,
            "candidate_version": self.candidate_version,
            "functional_requirements": list(self.functional_requirements),
            "input_contract": dict(self.input_contract),
            "output_contract": dict(self.output_contract),
            "permitted_files": [f.to_dict() for f in self.permitted_files],
            "dependencies": list(self.dependencies),
            "permitted_commands": [c.to_dict() for c in self.permitted_commands],
            "required_permissions": [p.value for p in self.required_permissions],
            "test_criteria": list(self.test_criteria),
            "resource_limits": self.resource_limits.to_dict(),
            "rollback_strategy": self.rollback_strategy,
            "created_at": self.created_at,
        }


class BuildPlanValidator:
    """Validates that a build plan is safe, authorized, and strictly bound to its proposal."""

    @staticmethod
    def validate_plan(
        plan: ExtensionBuildPlan,
        proposal: ExtensionProposal,
    ) -> Tuple[bool, List[str]]:
        """Validate a build plan against security policies and proposal integrity."""
        errors: List[str] = []

        # 1. Proposal state checks
        if proposal.status in (ProposalStatus.REJECTED, ProposalStatus.FAILED):
            errors.append(f"Cannot build plan for terminal proposal with status '{proposal.status.value}'.")

        # 2. Candidate hash binding check (prevents material mutation bypass)
        current_hash = ExtensionProposal.compute_candidate_hash(proposal.candidate)
        if current_hash != plan.candidate_hash:
            errors.append(
                f"Candidate hash mismatch: proposal hash '{current_hash}' != plan hash '{plan.candidate_hash}'. "
                "Candidate was materially modified."
            )

        if plan.proposal_id != proposal.proposal_id:
            errors.append(f"Plan proposal ID '{plan.proposal_id}' does not match proposal '{proposal.proposal_id}'.")

        # 3. Permitted files path validation (path traversal defense)
        if not plan.permitted_files:
            errors.append("Build plan specifies no permitted files.")

        for fspec in plan.permitted_files:
            rel = fspec.relative_path
            if not rel or rel == ".":
                errors.append(f"Invalid empty or root relative path: '{rel}'.")
            if ".." in rel.split("/") or ".." in rel.split("\\"):
                errors.append(f"Path traversal detected in permitted file: '{rel}'.")
            if os.path.isabs(rel) or (len(rel) > 1 and rel[1] == ":"):
                errors.append(f"Absolute path not permitted in workspace file spec: '{rel}'.")

        # 4. Command safety checks
        for cmd in plan.permitted_commands:
            cmd_lower = cmd.command.lower()
            if any(forbidden in cmd_lower for forbidden in ["format", "del", "rm", "rmdir", "curl", "powershell"]):
                errors.append(f"Dangerous or prohibited build command: '{cmd.command}'.")
            if cmd.requires_network and TaskPermission.NETWORK_ACCESS not in plan.required_permissions:
                errors.append(f"Command '{cmd.command}' requires network access but permission is not declared.")

        # 5. Resource limits bounds check
        limits = plan.resource_limits
        if limits.max_execution_time_seconds > 30.0:
            errors.append(f"Execution timeout {limits.max_execution_time_seconds}s exceeds maximum limit of 30s.")
        if limits.max_file_size_bytes > 5_000_000:
            errors.append(f"Max file size {limits.max_file_size_bytes} exceeds safety limit of 5 MB.")

        is_valid = len(errors) == 0
        return is_valid, errors
