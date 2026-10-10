"""Capability Gap Detection and Classification for Hey Jev (Phase 23).

Inspects failed or unplannable workflows and user goals to determine why
the desired outcome could not be achieved.

Distinguishes among:
1. EXISTING_UNDISCOVERED: Capability exists in registry but was not indexed/matched.
2. UNAVAILABLE_OR_UNHEALTHY: Capability exists but is unhealthy/degraded/offline.
3. MISSING_PREREQUISITE_OR_INPUT: Lacks required input data, file, or environment setup.
4. PERMISSION_DENIED: Caller lacks required authorization scope (NEVER solved by new tools).
5. GENUINELY_MISSING: Desired outcome has no implementation in the current system.
6. AMBIGUOUS_SPECIFICATION: Goal is vague, under-specified, or contradictory.
7. SAFETY_DISALLOWED: Action violates safety boundaries, denylists, or system policy.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_model import CapabilityDefinition, CapabilityType
from capability_registry import CapabilityRegistry
from capability_workflow import WorkflowPlan, WorkflowStep
from event_bus import EventBus
from tool_model import RiskLevel, ToolHealth
from workflow_engine import WorkflowExecutionReport, WorkflowExecutionStatus


STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
    "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being",
    "below", "between", "both", "but", "by", "can", "cannot", "could", "did", "do",
    "does", "doing", "down", "during", "each", "few", "for", "from", "further",
    "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "isn't", "it", "its",
    "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not",
    "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours",
    "ourselves", "out", "over", "own", "same", "she", "should", "so", "some",
    "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then",
    "there", "these", "they", "this", "those", "through", "to", "too", "under",
    "until", "up", "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "with", "would", "you", "your", "yours",
    "yourself", "yourselves", "please"
}


class GapType(str, Enum):
    """Taxonomy of capability and execution gaps."""
    EXISTING_UNDISCOVERED = "EXISTING_UNDISCOVERED"
    UNAVAILABLE_OR_UNHEALTHY = "UNAVAILABLE_OR_UNHEALTHY"
    MISSING_PREREQUISITE_OR_INPUT = "MISSING_PREREQUISITE_OR_INPUT"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    GENUINELY_MISSING = "GENUINELY_MISSING"
    AMBIGUOUS_SPECIFICATION = "AMBIGUOUS_SPECIFICATION"
    SAFETY_DISALLOWED = "SAFETY_DISALLOWED"


@dataclass
class CapabilityGap:
    """Structured record of an identified capability or execution gap."""
    gap_id: str
    gap_type: GapType
    desired_outcome: str
    missing_function: str
    evidence: List[str] = field(default_factory=list)
    confidence: float = 1.0  # 0.0 to 1.0
    affected_steps: List[str] = field(default_factory=list)
    available_alternatives: List[str] = field(default_factory=list)
    required_prerequisites: List[str] = field(default_factory=list)
    recommended_action: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "gap_type": self.gap_type.value,
            "desired_outcome": self.desired_outcome,
            "missing_function": self.missing_function,
            "evidence": list(self.evidence),
            "confidence": round(self.confidence, 2),
            "affected_steps": list(self.affected_steps),
            "available_alternatives": list(self.available_alternatives),
            "required_prerequisites": list(self.required_prerequisites),
            "recommended_action": self.recommended_action,
            "metadata": dict(self.metadata),
            "detected_at": self.detected_at,
        }


@dataclass
class GapAnalysisResult:
    """Comprehensive analysis outcome across a goal or failed workflow execution."""
    goal: str
    gaps: List[CapabilityGap] = field(default_factory=list)
    primary_gap: Optional[CapabilityGap] = None
    can_proceed: bool = False
    summary: str = ""
    analyzed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "gaps": [g.to_dict() for g in self.gaps],
            "primary_gap": self.primary_gap.to_dict() if self.primary_gap else None,
            "can_proceed": self.can_proceed,
            "summary": self.summary,
            "analyzed_at": self.analyzed_at,
        }


class CapabilityGapAnalyzer:
    """Analyzes failed workflows or unplannable goals to identify concrete gaps."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        event_bus: Optional[EventBus] = None,
        safety_engine: Optional[Any] = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus or EventBus()
        self.safety_engine = safety_engine

    def analyze_goal(
        self,
        goal: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> GapAnalysisResult:
        """Inspect a raw user goal and diagnose any capability or policy impediments."""
        granted = set(caller_permissions or [])
        gaps: List[CapabilityGap] = []
        clean_goal = (goal or "").strip()

        # 1. Ambiguity / Under-specification Check
        if not clean_goal or len(clean_goal) < 6 or clean_goal.lower() in ("do it", "help", "fix", "run"):
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.AMBIGUOUS_SPECIFICATION,
                desired_outcome=clean_goal or "Unspecified goal",
                missing_function="Concrete goal formulation",
                evidence=["Goal description is too brief or ambiguous to extract verifiable outcome."],
                confidence=0.95,
                recommended_action="Prompt user for specific desired outcome and target parameters.",
            )
            return GapAnalysisResult(
                goal=clean_goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary="Goal is insufficiently specified.",
            )

        # 2. Safety / Denylist Policy Check
        if self._is_safety_disallowed(clean_goal):
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.SAFETY_DISALLOWED,
                desired_outcome=clean_goal,
                missing_function="Policy-exempt execution handler",
                evidence=["Requested action violates system safety boundaries or shell denylists."],
                confidence=1.0,
                recommended_action="Refuse execution; explain policy restriction and safe alternative.",
            )
            return GapAnalysisResult(
                goal=clean_goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary="Action disallowed by system safety policy.",
            )

        # 3. Check for existing capabilities in registry
        existing_matches = self._find_candidate_capabilities(clean_goal)
        if existing_matches:
            # Check availability / health
            unavailable = [c for c in existing_matches if not c.is_available]
            available = [c for c in existing_matches if c.is_available]

            if available:
                # Check permissions of top candidate match
                c_top = available[0]
                missing_perms = [p for p in c_top.permissions if p not in granted]

                if missing_perms:
                    gap = CapabilityGap(
                        gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                        gap_type=GapType.PERMISSION_DENIED,
                        desired_outcome=clean_goal,
                        missing_function=f"Caller lacks required permission: {[p.value for p in missing_perms]}",
                        evidence=[
                            f"Capability '{c_top.name}' exists and is healthy, but caller has not been granted {[p.value for p in missing_perms]}."
                        ],
                        confidence=0.95,
                        available_alternatives=[c.capability_id for c in available],
                        required_prerequisites=[p.value for p in missing_perms],
                        recommended_action=(
                            f"Request human permission grant for {[p.value for p in missing_perms]}. "
                            "Do NOT acquire or install new tools."
                        ),
                    )
                    return GapAnalysisResult(
                        goal=clean_goal,
                        gaps=[gap],
                        primary_gap=gap,
                        can_proceed=False,
                        summary=f"Capability exists but execution lacks permission: {[p.value for p in missing_perms]}",
                    )
                else:
                    # Capability exists, is healthy, and permissions are sufficient!
                    return GapAnalysisResult(
                        goal=clean_goal,
                        gaps=[],
                        primary_gap=None,
                        can_proceed=True,
                        summary=f"Capability '{c_top.name}' exists and is fully available.",
                    )
            elif unavailable:
                c_unhealthy = unavailable[0]
                gap = CapabilityGap(
                    gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                    gap_type=GapType.UNAVAILABLE_OR_UNHEALTHY,
                    desired_outcome=clean_goal,
                    missing_function=f"Healthy state for '{c_unhealthy.name}'",
                    evidence=[
                        f"Capability '{c_unhealthy.capability_id}' exists but health is {c_unhealthy.health.value}."
                    ],
                    confidence=0.9,
                    available_alternatives=[c.capability_id for c in unavailable],
                    recommended_action=(
                        f"Repair or restore health for '{c_unhealthy.name}' ({c_unhealthy.health.value}) "
                        "before attempting execution."
                    ),
                )
                return GapAnalysisResult(
                    goal=clean_goal,
                    gaps=[gap],
                    primary_gap=gap,
                    can_proceed=False,
                    summary=f"Capability exists but is currently {c_unhealthy.health.value}.",
                )

        # 4. Check for Existing but Undiscovered Capability (alias / synonym mismatch)
        loose_match = self._find_undiscovered_capability(clean_goal)
        if loose_match:
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.EXISTING_UNDISCOVERED,
                desired_outcome=clean_goal,
                missing_function=f"Indexed query mapping to '{loose_match.name}'",
                evidence=[
                    f"Registered capability '{loose_match.capability_id}' supports related outcomes ({loose_match.supported_outcomes}) but did not match high-confidence query.",
                ],
                confidence=0.85,
                available_alternatives=[loose_match.capability_id],
                recommended_action=f"Route goal to existing capability '{loose_match.capability_id}' without new acquisition.",
            )
            return GapAnalysisResult(
                goal=clean_goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=True,
                summary=f"Existing capability '{loose_match.name}' can satisfy this goal with refined routing.",
            )

        # 5. Genuinely Missing Capability
        missing_domain = self._extract_domain(clean_goal)
        gap = CapabilityGap(
            gap_id=f"gap_{uuid.uuid4().hex[:8]}",
            gap_type=GapType.GENUINELY_MISSING,
            desired_outcome=clean_goal,
            missing_function=f"Capability to perform: {missing_domain}",
            evidence=[
                f"No registered tools or skills support the outcome '{clean_goal}'.",
                f"Registry searched across {self.registry.count()} capabilities with 0 viable matches.",
            ],
            confidence=0.9,
            recommended_action="Investigate trusted integrations, local adapters, or prepare an extension proposal.",
            metadata={"domain": missing_domain},
        )
        return GapAnalysisResult(
            goal=clean_goal,
            gaps=[gap],
            primary_gap=gap,
            can_proceed=False,
            summary=f"Genuinely missing capability for: {missing_domain}",
        )

    def analyze_execution_report(
        self,
        report: WorkflowExecutionReport,
        plan: Optional[WorkflowPlan] = None,
        caller_permissions: Optional[List[TaskPermission]] = None,
    ) -> GapAnalysisResult:
        """Inspect a failed or paused workflow execution report to pinpoint the exact failure cause."""
        granted = set(caller_permissions or [])
        err_msg = (report.error or "").lower()

        # Check for Policy Abort due to missing approval
        if report.status == WorkflowExecutionStatus.ABORTED_POLICY and "approval checkpoint" in err_msg:
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.PERMISSION_DENIED,
                desired_outcome=report.goal,
                missing_function="Human approval grant for gated action",
                evidence=[report.error or "Policy checkpoint unfulfilled."],
                affected_steps=[report.waiting_on_step] if report.waiting_on_step else [],
                recommended_action="Obtain explicit human approval for this specific step before resuming.",
            )
            return GapAnalysisResult(
                goal=report.goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary="Execution halted pending explicit user approval checkpoint.",
            )

        # Check for Missing Permissions error
        if "missing required permission" in err_msg:
            m = re.search(r"permission\(s\):\s*\[(.*?)\]", report.error or "")
            perms_str = m.group(1) if m else "REQUIRED_PERMISSIONS"
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.PERMISSION_DENIED,
                desired_outcome=report.goal,
                missing_function=f"Caller authorization: {perms_str}",
                evidence=[report.error or "Missing required permissions."],
                recommended_action="Grant the necessary task permissions. Do NOT install new tools.",
            )
            return GapAnalysisResult(
                goal=report.goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary=f"Execution failed due to missing permission: {perms_str}",
            )

        # Check for Missing Input / Reference error
        if "unresolved variable reference" in err_msg or "missing required argument" in err_msg:
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.MISSING_PREREQUISITE_OR_INPUT,
                desired_outcome=report.goal,
                missing_function="Step inputs or data-pipelining variable",
                evidence=[report.error or "Input resolution failure."],
                recommended_action="Verify step dependencies and ensure prerequisite steps supply the required schema.",
            )
            return GapAnalysisResult(
                goal=report.goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary="Workflow failed due to missing prerequisite input or unresolved variable.",
            )

        # Check for Unregistered Capability error
        if "is not registered" in err_msg:
            m = re.search(r"capability '([^']+)' is not registered", report.error or "", re.IGNORECASE)
            missing_cap_id = m.group(1) if m else "unknown_capability"
            gap = CapabilityGap(
                gap_id=f"gap_{uuid.uuid4().hex[:8]}",
                gap_type=GapType.GENUINELY_MISSING,
                desired_outcome=report.goal,
                missing_function=f"Capability implementation for '{missing_cap_id}'",
                evidence=[report.error or f"Capability '{missing_cap_id}' not found in registry."],
                recommended_action=f"Discover candidate implementation or extension proposal for '{missing_cap_id}'.",
            )
            return GapAnalysisResult(
                goal=report.goal,
                gaps=[gap],
                primary_gap=gap,
                can_proceed=False,
                summary=f"Workflow failed because capability '{missing_cap_id}' does not exist.",
            )

        # Fallback to general goal analysis
        return self.analyze_goal(report.goal, caller_permissions=caller_permissions)

    def _is_safety_disallowed(self, goal: str) -> bool:
        """Evaluate whether goal asks for dangerous, destructive, or prohibited actions."""
        g_lower = goal.lower()
        prohibited_patterns = [
            r"\bformat\s+[a-z]:",
            r"\bdiskpart\b",
            r"\brmdir\s+/[sS]\s+/[qQ]\s+[a-zA-Z]:\\windows",
            r"\bdel\s+.*system32",
            r"\bnet\s+user\s+.*\/add",
            r"\breg\s+delete\s+hklm",
            r"\bdownload\s+cradle\b",
            r"\biex\s*\(",
            r"\binvoke-expression\b",
            r"\bwin\+r\b",
            r"\brun\s+dialog\b",
            r"\bbypass\s+(antivirus|defender|uac|firewall)\b",
            r"\bdisable\s+(defender|firewall)\b",
        ]
        for pat in prohibited_patterns:
            if re.search(pat, g_lower):
                return True

        if self.safety_engine and hasattr(self.safety_engine, "is_command_safe"):
            if not self.safety_engine.is_command_safe(goal):
                return True

        return False

    def _find_candidate_capabilities(self, goal: str) -> List[CapabilityDefinition]:
        """Find capabilities matching the goal description."""
        g_words = {w for w in re.findall(r"\w+", goal.lower()) if w not in STOPWORDS and len(w) > 2}
        if not g_words:
            return []
        matches: List[Tuple[float, CapabilityDefinition]] = []

        for cap in self.registry.list():
            score = 0.0
            # Direct outcome match
            for out in cap.supported_outcomes:
                out_words = {w for w in re.findall(r"\w+", out.lower()) if w not in STOPWORDS}
                common_out = g_words.intersection(out_words)
                if common_out:
                    score += len(common_out) * 3.0

            # Name match
            name_words = {w for w in re.findall(r"\w+", cap.name.lower()) if w not in STOPWORDS}
            overlap = len(g_words.intersection(name_words))
            score += overlap * 2.5

            # Description match
            desc_words = {w for w in re.findall(r"\w+", cap.description.lower()) if w not in STOPWORDS}
            desc_overlap = len(g_words.intersection(desc_words))
            score += desc_overlap * 0.5

            if score >= 3.0:
                matches.append((score, cap))

        matches.sort(key=lambda item: -item[0])
        return [m[1] for m in matches]

    def _find_undiscovered_capability(self, goal: str) -> Optional[CapabilityDefinition]:
        """Detect capabilities that could satisfy the goal via synonym or alias matching."""
        synonym_map = {
            "see": "list",
            "show": "list",
            "browse": "list",
            "relocate": "move",
            "transfer": "move",
            "shift": "move",
            "background": "wallpaper",
            "backdrop": "wallpaper",
            "desktop_pic": "wallpaper",
        }
        g_words = {w for w in re.findall(r"\w+", goal.lower()) if w not in STOPWORDS}
        expanded_words = set(g_words)
        for w in g_words:
            if w in synonym_map:
                expanded_words.add(synonym_map[w])

        for cap in self.registry.list(only_available=True):
            for out in cap.supported_outcomes:
                if any(ew in out.lower() for ew in expanded_words):
                    return cap
            for tag in cap.tags:
                if tag.lower() in expanded_words:
                    return cap

        return None

    def _extract_domain(self, goal: str) -> str:
        """Extract primary functional domain keyword from goal."""
        words = re.findall(r"\w+", goal.lower())
        meaningful = [w for w in words if w not in STOPWORDS and len(w) > 2]
        return " ".join(meaningful[:4]) if meaningful else "unknown_task"
