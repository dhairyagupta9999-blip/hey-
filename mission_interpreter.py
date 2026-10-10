"""Outcome-Oriented Goal Interpreter for Hey Jev (Phase 27).

Transforms natural-language user objectives into structured, measurable missions:
- Normalizes desired outcome and intent
- Synthesizes explicit, measurable acceptance criteria
- Distinguishes explicit vs inferred constraints
- Identifies candidate capabilities and detects capability gaps
- Flags material risks, irreversible actions, and approval checkpoints
- Asks concise questions ONLY on material ambiguities, otherwise uses documented reversible defaults
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from capability_discovery import CapabilityDiscoveryEngine, DiscoveryResult
from capability_registry import CapabilityRegistry
from memory_model import sanitize_content
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionBudget,
    MissionMode,
    MissionStatus,
)
from tool_model import RiskLevel


class MissionInterpreter:
    """Interprets high-level user objectives into structured missions with verifiable outcomes."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        discovery_engine: Optional[CapabilityDiscoveryEngine] = None,
        extension_manager: Optional[Any] = None,
    ) -> None:
        self.registry = registry
        self.discovery_engine = discovery_engine or CapabilityDiscoveryEngine(registry=registry)
        self.extension_manager = extension_manager

    def interpret_objective(
        self,
        objective: str,
        context: Optional[Dict[str, Any]] = None,
        mode: MissionMode = MissionMode.COPILOT,
        caller_permissions: Optional[List[str]] = None,
        budget: Optional[MissionBudget] = None,
    ) -> Tuple[Mission, Dict[str, Any]]:
        """Interpret a natural-language user objective into a structured Mission.

        Returns:
            (mission, interpretation_metadata)
        """
        raw_obj = str(objective or "").strip()
        ctx = dict(context or {})
        perms = list(caller_permissions or [])

        # 1. Normalize goal
        normalized_goal = self._normalize_goal(raw_obj)

        # 2. Extract explicit and inferred constraints
        explicit_constraints, inferred_constraints = self._extract_constraints(raw_obj, ctx)

        # 3. Synthesize measurable acceptance criteria
        acceptance_criteria = self._synthesize_acceptance_criteria(normalized_goal, raw_obj, ctx)

        # 4. Capability discovery and gap detection
        discovery = self.discovery_engine.discover(
            goal=normalized_goal,
            context=ctx,
        )

        blockers: List[str] = []
        assumptions: List[str] = []
        unresolved_questions: List[str] = []
        required_permissions: List[str] = list(perms)

        if not discovery.best_match:
            # Check for capability gap
            reason = discovery.unsupported_reason or f"No existing capability found for '{normalized_goal}'."
            blockers.append(f"Capability gap: {reason}")
            if self.extension_manager is not None:
                assumptions.append(
                    "Identified capability gap; extension proposal pipeline can be queried for acquisition."
                )
        else:
            best = discovery.best_match
            if best.capability.permissions:
                for p in best.capability.permissions:
                    p_val = p.value if hasattr(p, "value") else str(p)
                    if p_val not in required_permissions:
                        required_permissions.append(p_val)

        # 5. Material ambiguity detection & questions vs safe defaults
        is_materially_ambiguous, questions, safe_defaults = self._analyze_ambiguity(raw_obj, ctx)
        if is_materially_ambiguous:
            unresolved_questions.extend(questions)
        else:
            assumptions.extend(safe_defaults)

        # 6. Default inferred constraints
        inferred_constraints.append("Enforce least privilege and sanitize all logs and outputs.")
        inferred_constraints.append("Never execute irreversible destructive actions without verification.")

        mission = Mission(
            mission_id=f"mission_{uuid.uuid4().hex[:12]}",
            objective=raw_obj,
            normalized_goal=normalized_goal,
            explicit_constraints=explicit_constraints,
            inferred_constraints=inferred_constraints,
            acceptance_criteria=acceptance_criteria,
            plan=None,
            mode=mode,
            status=MissionStatus.PLANNING,
            status_reason="Interpreted objective; ready for DAG planning.",
            budget=budget or MissionBudget(),
            required_permissions=required_permissions,
            blockers=blockers,
            assumptions=assumptions,
            unresolved_questions=unresolved_questions,
        )

        metadata = {
            "discovery_confidence": discovery.confidence,
            "best_capability": discovery.best_match.capability.capability_id if discovery.best_match else None,
            "is_ambiguous": discovery.is_ambiguous or is_materially_ambiguous,
            "candidates_count": len(discovery.candidates),
        }

        return mission, metadata

    def _normalize_goal(self, objective: str) -> str:
        """Strip conversational filler and extract core actionable intent."""
        text = objective.strip()
        # Remove common conversational prefixes
        prefixes = [
            r"^(hey\s+jev|jev|please|can\s+you|could\s+you|i\s+want\s+to|i\s+need\s+to|help\s+me\s+to)\s*[,:]?\s*",
            r"^(would\s+you|kindly|go\s+ahead\s+and)\s*",
        ]
        for p in prefixes:
            text = re.sub(p, "", text, flags=re.IGNORECASE).strip()

        # Clean multiple spaces
        text = re.sub(r"\s+", " ", text)
        return text if text else objective

    def _extract_constraints(
        self, objective: str, context: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """Separate explicit user constraints from inferred system constraints."""
        explicit: List[str] = []
        inferred: List[str] = []

        lower = objective.lower()

        # Explicit negative constraints ("do not", "never", "without", "only")
        neg_matches = re.findall(
            r"(?:do not|don't|never|without|only|except)\s+([^,.;]+)", lower
        )
        for match in neg_matches:
            cleaned = match.strip()
            if cleaned:
                explicit.append(f"Constraint: do not {cleaned}")

        if "keep" in lower and "under" in lower:
            explicit.append("Constraint: maintain specified budget/limit")

        # Inferred constraints based on domain
        if any(w in lower for w in ["delete", "remove", "clean", "purge"]):
            inferred.append("Ensure target files are strictly confined to temporary or requested paths.")
            inferred.append("Preserve user documents, source repositories, and configuration files.")

        if any(w in lower for w in ["deploy", "install", "update", "upgrade"]):
            inferred.append("Validate deployment health and verify service responsiveness.")
            inferred.append("Keep previous version available for rollback if deployment fails.")

        if any(w in lower for w in ["benchmark", "test", "analyze"]):
            inferred.append("Execute within CPU and memory budget to prevent foreground degradation.")

        return explicit, inferred

    def _synthesize_acceptance_criteria(
        self, normalized_goal: str, raw_objective: str, context: Dict[str, Any]
    ) -> List[AcceptanceCriterion]:
        """Synthesize explicit, observable acceptance criteria."""
        criteria: List[AcceptanceCriterion] = []
        lower = normalized_goal.lower()

        idx = 1
        # Check custom explicit verification in context or goal
        if "and verify" in lower:
            verify_part = lower.split("and verify")[-1].strip()
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description=f"Explicitly verified: {verify_part}",
                    required=True,
                )
            )
            idx += 1

        if any(w in lower for w in ["clean", "remove", "delete", "purge"]):
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Target files identified matching requested criteria without touching protected paths.",
                    required=True,
                )
            )
            idx += 1
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Specified targets successfully deleted and disk space reclaimed.",
                    required=True,
                )
            )
            idx += 1
        elif any(w in lower for w in ["deploy", "release", "ship"]):
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Build or package artifacts compiled and validated.",
                    required=True,
                )
            )
            idx += 1
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Application successfully deployed to target destination.",
                    required=True,
                )
            )
            idx += 1
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Post-deployment health checks return healthy.",
                    required=True,
                )
            )
            idx += 1
        elif any(w in lower for w in ["test", "verify", "audit"]):
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Test suite or audit checks executed to completion.",
                    required=True,
                )
            )
            idx += 1
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="All tests passed with zero unexpected regressions.",
                    required=True,
                )
            )
            idx += 1
        else:
            # General outcome criteria
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description=f"Actionable outcome for '{normalized_goal}' successfully executed.",
                    required=True,
                )
            )
            idx += 1
            criteria.append(
                AcceptanceCriterion(
                    criterion_id=f"ac-{idx}",
                    description="Physical or programmatic output matches expected success condition.",
                    required=True,
                )
            )
            idx += 1

        return criteria

    def _analyze_ambiguity(
        self, objective: str, context: Dict[str, Any]
    ) -> Tuple[bool, List[str], List[str]]:
        """Identify material ambiguities vs reasonable defaults.

        Returns:
            (is_materially_ambiguous, questions, safe_defaults)
        """
        lower = objective.lower()
        questions: List[str] = []
        safe_defaults: List[str] = []

        # High-impact ambiguity check: e.g. "delete all files" without specifying path
        if re.search(r"\b(delete|remove|wipe|purge|clear)\s+(all|everything|all files)\b", lower):
            if "path" not in context and "folder" not in lower and "directory" not in lower:
                questions.append(
                    "Which specific directory should be cleaned? Please specify the target path."
                )
                return True, questions, safe_defaults

        # If goal mentions "format" or "wipe disk"
        if re.search(r"\b(format|wipe)\s+(disk|drive|partition)\b", lower):
            questions.append("Destructive disk operation requested. Please confirm target drive letter.")
            return True, questions, safe_defaults

        # Non-material ambiguities -> use documented safe defaults
        if "temporary" in lower or "cache" in lower:
            safe_defaults.append(
                "Defaulting cleanup to system temporary directory; preserving files created within last 24 hours."
            )
        else:
            safe_defaults.append(
                "Defaulting to standard non-destructive copilot verification before high-impact changes."
            )

        return False, questions, safe_defaults
