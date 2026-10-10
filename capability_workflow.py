"""Capability Workflow Composition, Plan Representation, and Modes for Hey Jev (Phase 22).

Enables dynamic composition of discovered capabilities into a dependency-aware,
verifiable workflow DAG.

Provides:
- WorkflowMode enum (MANUAL, COPILOT, AUTOPILOT)
- WorkflowStep with input data-pipelining, conditional branching, and verification criteria
- WorkflowPlan with DAG topological sequencing, permission rollup, and readable preview
- WorkflowComposer for multi-capability goal decomposition and DAG synthesis
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_discovery import CapabilityDiscoveryEngine, DiscoveryResult
from capability_model import (
    ApprovalRequirement,
    CapabilityDefinition,
    CapabilityType,
    Reversibility,
)
from capability_registry import CapabilityRegistry
from tool_model import RiskLevel


class WorkflowMode(str, Enum):
    """Operational mode governing autonomy and approval gates."""
    MANUAL = "MANUAL"        # Prepare plan; human initiates individual steps
    COPILOT = "COPILOT"      # Automatic execution of safe steps; prompt for gated/risky actions
    AUTOPILOT = "AUTOPILOT"  # Execute pre-authorized safe steps within policy; abort if gated/unauthorized


class StepStatus(str, Enum):
    """Execution lifecycle status of an individual workflow step."""
    PENDING = "PENDING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class WorkflowStep:
    """Atomic step in a composed workflow DAG with data pipelining and verification."""
    step_id: str
    capability_id: str
    description: str
    inputs: Dict[str, Any] = field(default_factory=dict)
    depends_on: List[str] = field(default_factory=list)
    condition: Optional[Dict[str, Any]] = None  # e.g. {"key": "$step1.count", "op": ">", "val": 0}
    approval_checkpoint: bool = False
    approval_reason: str = ""
    expected_outcome: str = ""
    verification_criteria: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    timeout_s: float = 30.0
    status: StepStatus = StepStatus.PENDING
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    verified: bool = False
    verification_evidence: Dict[str, Any] = field(default_factory=dict)
    execution_duration_ms: float = 0.0
    rollback_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "capability_id": self.capability_id,
            "description": self.description,
            "inputs": self.inputs,
            "depends_on": list(self.depends_on),
            "condition": self.condition,
            "approval_checkpoint": self.approval_checkpoint,
            "approval_reason": self.approval_reason,
            "expected_outcome": self.expected_outcome,
            "verification_criteria": self.verification_criteria,
            "retry_count": self.retry_count,
            "timeout_s": self.timeout_s,
            "status": self.status.value,
            "output": self.output,
            "error": self.error,
            "verified": self.verified,
            "duration_ms": round(self.execution_duration_ms, 2),
        }


@dataclass
class WorkflowPlan:
    """Declarative DAG workflow plan composed of ordered capability steps."""
    workflow_id: str
    goal: str
    desired_outcome: str
    mode: WorkflowMode = WorkflowMode.COPILOT
    steps: List[WorkflowStep] = field(default_factory=list)
    required_permissions: List[TaskPermission] = field(default_factory=list)
    max_risk_level: RiskLevel = RiskLevel.LOW
    requires_approval: bool = False
    approval_reasons: List[str] = field(default_factory=list)
    plan_summary: str = ""
    created_at: float = field(default_factory=time.time)

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def get_topological_order(self) -> List[str]:
        """Return step IDs in strict topological execution order."""
        in_degrees: Dict[str, int] = {s.step_id: len(s.depends_on) for s in self.steps}
        dependents: Dict[str, List[str]] = {s.step_id: [] for s in self.steps}

        for s in self.steps:
            for dep in s.depends_on:
                if dep in dependents:
                    dependents[dep].append(s.step_id)

        queue = [s_id for s_id, deg in in_degrees.items() if deg == 0]
        order = []

        while queue:
            curr = queue.pop(0)
            order.append(curr)
            for child in dependents.get(curr, []):
                in_degrees[child] -= 1
                if in_degrees[child] == 0:
                    queue.append(child)

        if len(order) != len(self.steps):
            raise ValueError("Workflow contains cyclical dependencies; cannot order topologically.")
        return order

    def get_readable_preview(self) -> str:
        """Generate human-readable summary of the plan, steps, risks, and approval gates."""
        lines = [
            f"=== WORKFLOW PREVIEW [{self.workflow_id}] ===",
            f"Goal: {self.goal}",
            f"Mode: {self.mode.value}",
            f"Max Risk: {self.max_risk_level.value}",
            f"Approval Required: {'YES' if self.requires_approval else 'NO'}",
        ]
        if self.approval_reasons:
            lines.append(f"Approval Reasons: {'; '.join(self.approval_reasons)}")
        lines.append("\nExecution Steps:")

        try:
            ordered_ids = self.get_topological_order()
            steps_dict = {s.step_id: s for s in self.steps}
            for i, s_id in enumerate(ordered_ids, 1):
                s = steps_dict[s_id]
                gate_str = " [APPROVAL GATE]" if s.approval_checkpoint else ""
                dep_str = f" (after {', '.join(s.depends_on)})" if s.depends_on else ""
                lines.append(f"  {i}. [{s.capability_id}] {s.description}{dep_str}{gate_str}")
                if s.inputs:
                    lines.append(f"     Inputs: {s.inputs}")
                if s.verification_criteria:
                    lines.append(f"     Verify: {', '.join(s.verification_criteria)}")
        except Exception as e:
            lines.append(f"  [Error sorting steps: {e}]")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "goal": self.goal,
            "desired_outcome": self.desired_outcome,
            "mode": self.mode.value,
            "steps_count": len(self.steps),
            "steps": [s.to_dict() for s in self.steps],
            "required_permissions": [p.value for p in self.required_permissions],
            "max_risk_level": self.max_risk_level.value,
            "requires_approval": self.requires_approval,
            "approval_reasons": self.approval_reasons,
            "preview": self.get_readable_preview(),
        }


class WorkflowComposer:
    """Decomposes outcome goals and composes discovered capabilities into dependency DAG workflows."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        discovery_engine: Optional[CapabilityDiscoveryEngine] = None,
    ) -> None:
        self.registry = registry
        self.discovery_engine = discovery_engine or CapabilityDiscoveryEngine(registry=registry)

    def compose_workflow(
        self,
        goal: str,
        context: Optional[Dict[str, Any]] = None,
        mode: WorkflowMode = WorkflowMode.COPILOT,
        caller_permissions: Optional[List[TaskPermission]] = None,
        max_risk: Optional[RiskLevel] = None,
    ) -> Tuple[Optional[WorkflowPlan], Optional[str]]:
        """Synthesize a complete WorkflowPlan for the given goal.

        Returns:
            (WorkflowPlan, None) on success
            (None, error_reason) on failure
        """
        ctx = dict(context or {})
        clean_goal = str(goal).strip()
        wf_id = f"wf_{uuid.uuid4().hex[:10]}"

        # 1. Discover top candidate capabilities
        discovery = self.discovery_engine.discover(
            goal=clean_goal,
            caller_permissions=caller_permissions,
            max_risk=max_risk,
            context=ctx,
        )

        if not discovery.best_match:
            return None, discovery.unsupported_reason or "No matching capability found."

        # If discovery is ambiguous and no explicit resolution, fail safely with clarification
        if discovery.is_ambiguous and not ctx.get("resolved_capability"):
            cands_str = ", ".join(discovery.ambiguity_candidates)
            return None, f"Ambiguous goal matches multiple competing plans: {cands_str}. Please clarify."

        # 2. Multi-step Goal Synthesis Detection
        # Check compound outcome patterns
        g_lower = clean_goal.lower()
        is_find_and_wallpaper = ("wallpaper" in g_lower or "background" in g_lower) and any(
            w in g_lower for w in ["find", "picture", "photo", "image", "download"]
        )
        is_scan_and_organize = any(w in g_lower for w in ["organize", "clean up", "sort"]) and any(
            w in g_lower for w in ["download", "folder", "files"]
        )
        is_research_and_summarize = any(w in g_lower for w in ["research", "compare", "investigate"])

        steps: List[WorkflowStep] = []
        all_perms: Set[TaskPermission] = set()
        approval_reasons: List[str] = []
        max_seen_risk = RiskLevel.LOW
        risk_ranks = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}

        # Pattern A: Multi-step Find & Set Wallpaper
        if is_find_and_wallpaper:
            list_cap = self.registry.get("filesystem.list_directory")
            wall_cap = self.registry.get("windows.set_wallpaper")

            if list_cap and wall_cap:
                folder_path = ctx.get("path") or ctx.get("folder") or "Downloads"
                step1 = WorkflowStep(
                    step_id="step_list_photos",
                    capability_id=list_cap.capability_id,
                    description=f"Scan directory '{folder_path}' for picture files",
                    inputs={"path": folder_path},
                    expected_outcome="list_pictures_in_directory",
                    verification_criteria=["entries_is_list"],
                )

                # Step 2: Set wallpaper from output of step 1
                target_idx = ctx.get("photo_index", 4)  # e.g., 4th picture or newest
                step2 = WorkflowStep(
                    step_id="step_apply_wallpaper",
                    capability_id=wall_cap.capability_id,
                    description=f"Apply selected photo (item #{target_idx}) as desktop wallpaper",
                    inputs={
                        "path": f"$step_list_photos.entries[{target_idx - 1}].path",
                        "folder": folder_path,
                        "index": target_idx,
                    },
                    depends_on=["step_list_photos"],
                    approval_checkpoint=(wall_cap.risk_level in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)),
                    approval_reason="Modifying system desktop wallpaper",
                    expected_outcome="wallpaper_updated",
                    verification_criteria=["wallpaper_path_set_in_registry"],
                )
                steps = [step1, step2]

        # Pattern B: Scan & Move / Organize
        elif is_scan_and_organize:
            list_cap = self.registry.get("filesystem.list_directory")
            move_cap = self.registry.get("filesystem.move_file")

            if list_cap and move_cap:
                src_path = ctx.get("path") or "Downloads"
                dest_path = ctx.get("destination") or "Organized"
                step1 = WorkflowStep(
                    step_id="step_scan_folder",
                    capability_id=list_cap.capability_id,
                    description=f"Scan source directory '{src_path}' for items to organize",
                    inputs={"path": src_path},
                    expected_outcome="discover_files_to_organize",
                    verification_criteria=["entries_is_list"],
                )
                step2 = WorkflowStep(
                    step_id="step_organize_items",
                    capability_id=move_cap.capability_id,
                    description=f"Move categorized files into '{dest_path}'",
                    inputs={
                        "source": f"$step_scan_folder.entries[0].path",
                        "destination": dest_path,
                    },
                    depends_on=["step_scan_folder"],
                    approval_checkpoint=(move_cap.risk_level != RiskLevel.LOW),
                    approval_reason="Moving/relocating files on disk",
                    expected_outcome="files_organized",
                    verification_criteria=["file_moved_to_destination"],
                )
                steps = [step1, step2]

        # Pattern C: Default Single or Direct Skill Workflow
        if not steps:
            # Use top discovered capability
            best_cap = discovery.best_match.capability
            # Resolve inputs from context
            step_inputs = {}
            for req_arg in best_cap.required_arguments:
                if req_arg in ctx:
                    step_inputs[req_arg] = ctx[req_arg]
                elif f"${req_arg}" in ctx:
                    step_inputs[req_arg] = ctx[f"${req_arg}"]

            needs_gate = (
                best_cap.approval_requirement == ApprovalRequirement.MANDATORY
                or (best_cap.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL))
                or (mode == WorkflowMode.COPILOT and best_cap.risk_level == RiskLevel.MEDIUM)
            )
            step1 = WorkflowStep(
                step_id="step_1",
                capability_id=best_cap.capability_id,
                description=f"Execute {best_cap.name}: {clean_goal}",
                inputs=step_inputs,
                approval_checkpoint=needs_gate,
                approval_reason=f"Risk level {best_cap.risk_level.value} requiring authorization",
                expected_outcome=best_cap.supported_outcomes[0] if best_cap.supported_outcomes else "success",
                verification_criteria=list(best_cap.observable_success_criteria),
            )
            steps = [step1]

        # Roll up permissions, risk, and approval requirements across all steps
        for step in steps:
            cap = self.registry.get(step.capability_id)
            if cap:
                for p in cap.permissions:
                    all_perms.add(p)
                if risk_ranks.get(cap.risk_level, 1) > risk_ranks.get(max_seen_risk, 1):
                    max_seen_risk = cap.risk_level
                if step.approval_checkpoint:
                    approval_reasons.append(step.approval_reason or f"Step '{step.step_id}' ({cap.risk_level.value})")

        plan = WorkflowPlan(
            workflow_id=wf_id,
            goal=clean_goal,
            desired_outcome=steps[-1].expected_outcome if steps else "completed",
            mode=mode,
            steps=steps,
            required_permissions=sorted(list(all_perms), key=lambda x: x.value),
            max_risk_level=max_seen_risk,
            requires_approval=len(approval_reasons) > 0,
            approval_reasons=approval_reasons,
            plan_summary=f"Plan with {len(steps)} steps for: {clean_goal}",
        )

        return plan, None
