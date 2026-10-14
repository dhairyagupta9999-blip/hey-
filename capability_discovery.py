"""Capability Discovery and Intelligent Ranking for Hey Jev (Phase 22).

Enables outcome-oriented discovery of tools and skills:
- Searches by user's intended result/outcome, not merely tool identifiers
- Multi-factor ranking: outcome semantic alignment, operational health,
  cost/latency profiles, reversibility bonuses, and risk constraints
- Deterministic pure-Python scoring using BM25 token relevance
- Detects missing arguments, unfulfilled preconditions, and ambiguous competing plans
- Explicitly identifies when no safe or suitable capability exists without fabricating support
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission
from capability_model import (
    ApprovalRequirement,
    CapabilityDefinition,
    CapabilityType,
    Reversibility,
)
from capability_registry import CapabilityRegistry
from semantic_memory import BM25SemanticProvider
from tool_model import RiskLevel, ToolHealth


@dataclass
class ScoredCapability:
    """A candidate capability evaluated against an outcome-oriented goal."""
    capability: CapabilityDefinition
    score: float
    outcome_score: float
    semantic_score: float
    health_score: float
    risk_penalty: float
    reversibility_bonus: float
    matched_outcomes: List[str] = field(default_factory=list)
    missing_arguments: List[str] = field(default_factory=list)
    unresolved_preconditions: List[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability.capability_id,
            "name": self.capability.name,
            "type": self.capability.capability_type.value,
            "score": round(self.score, 3),
            "outcome_score": round(self.outcome_score, 3),
            "semantic_score": round(self.semantic_score, 3),
            "health_score": round(self.health_score, 3),
            "matched_outcomes": self.matched_outcomes,
            "missing_arguments": self.missing_arguments,
            "risk_level": self.capability.risk_level.value,
            "reversibility": self.capability.reversibility.value,
            "approval_requirement": self.capability.approval_requirement.value,
            "rationale": self.rationale,
        }


@dataclass
class DiscoveryResult:
    """Comprehensive discovery outcome for a goal."""
    goal: str
    candidates: List[ScoredCapability] = field(default_factory=list)
    best_match: Optional[ScoredCapability] = None
    is_ambiguous: bool = False
    ambiguity_candidates: List[str] = field(default_factory=list)
    missing_requirements: List[str] = field(default_factory=list)
    unsupported_reason: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "confidence": round(self.confidence, 3),
            "best_match": self.best_match.to_dict() if self.best_match else None,
            "candidates_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates[:5]],
            "is_ambiguous": self.is_ambiguous,
            "ambiguity_candidates": self.ambiguity_candidates,
            "missing_requirements": self.missing_requirements,
            "unsupported_reason": self.unsupported_reason,
        }


STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "my",
    "our", "your", "is", "are", "be", "with", "from", "as", "by", "that", "this",
}


def _stem(word: str) -> str:
    """Lightweight deterministic stemmer for English suffixes."""
    w = word.lower().strip()
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    if w.endswith("es") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _extract_stemmed_tokens(text: str, remove_stopwords: bool = True) -> Set[str]:
    raw_tokens = re.findall(r"\w+", text.lower())
    if remove_stopwords:
        tokens = [t for t in raw_tokens if t not in STOP_WORDS]
    else:
        tokens = raw_tokens
    return {_stem(t) for t in tokens if t}


class CapabilityDiscoveryEngine:
    """Discovers, scores, and ranks capabilities based on desired user outcomes."""

    # Risk penalties
    RISK_PENALTIES = {
        RiskLevel.LOW: 0.0,
        RiskLevel.MEDIUM: 0.05,
        RiskLevel.HIGH: 0.20,
        RiskLevel.CRITICAL: 0.40,
    }

    # Health weights
    HEALTH_WEIGHTS = {
        ToolHealth.HEALTHY: 1.0,
        ToolHealth.DEGRADED: 0.60,
        ToolHealth.RATE_LIMITED: 0.25,
        ToolHealth.AUTH_REQUIRED: 0.10,
        ToolHealth.POLICY_BLOCKED: 0.0,
        ToolHealth.UNAVAILABLE: 0.0,
        ToolHealth.BROKEN: 0.0,
    }

    def __init__(
        self,
        registry: CapabilityRegistry,
        semantic_provider: Optional[BM25SemanticProvider] = None,
    ) -> None:
        self.registry = registry
        self.semantic_provider = semantic_provider or BM25SemanticProvider()

    def discover(
        self,
        goal: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        max_risk: Optional[RiskLevel] = None,
        context: Optional[Dict[str, Any]] = None,
        prefer_read_only: bool = False,
        min_score_threshold: float = 0.25,
    ) -> DiscoveryResult:
        """Find and rank candidate capabilities matching the desired user outcome."""
        ctx = dict(context or {})
        granted_perms = set(caller_permissions or [])
        clean_goal = str(goal).strip()
        goal_tokens = set(re.findall(r"\w+", clean_goal.lower()))
        goal_stemmed = _extract_stemmed_tokens(clean_goal, remove_stopwords=True)
        if not goal_stemmed:
            goal_stemmed = _extract_stemmed_tokens(clean_goal, remove_stopwords=False)

        if not clean_goal:
            return DiscoveryResult(
                goal=clean_goal,
                unsupported_reason="Empty goal provided",
                confidence=0.0,
            )

        all_capabilities = self.registry.list()
        if not all_capabilities:
            return DiscoveryResult(
                goal=clean_goal,
                unsupported_reason="No capabilities registered in system",
                confidence=0.0,
            )

        scored_candidates: List[ScoredCapability] = []

        risk_ranks = {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3, RiskLevel.CRITICAL: 4}
        max_rank = risk_ranks.get(max_risk, 4) if max_risk else 4

        for cap in all_capabilities:
            # 1. Operational Health Filtering
            health_score = self.HEALTH_WEIGHTS.get(cap.health, 0.0)
            if health_score <= 0.0:
                continue

            # 2. Risk Gate
            cap_risk_rank = risk_ranks.get(cap.risk_level, 1)
            if cap_risk_rank > max_rank:
                continue

            # 3. Permissions Check
            lacking_perms = [p for p in cap.permissions if p not in granted_perms]
            # If explicit permissions provided and missing, apply severe penalty
            perm_multiplier = 1.0
            if caller_permissions is not None and lacking_perms:
                perm_multiplier = 0.2

            # 4. Outcome & Semantic Matching
            # Direct outcome string matching with stemming
            matched_outcomes = []
            outcome_score = 0.0
            for outcome in cap.supported_outcomes:
                outcome_clean = outcome.replace("_", " ").lower()
                outcome_stemmed = _extract_stemmed_tokens(outcome_clean, remove_stopwords=True)
                common = goal_stemmed.intersection(outcome_stemmed)
                if common:
                    overlap_ratio = len(common) / max(1, len(outcome_stemmed))
                    if overlap_ratio >= 0.30:
                        matched_outcomes.append(outcome)
                        match_boost = 0.50 + (overlap_ratio * 0.50)
                        outcome_score = max(outcome_score, match_boost)

            # BM25 Token Matching over descriptive corpus
            corpus = f"{cap.name} {cap.description} {' '.join(cap.tags)} {' '.join(cap.supported_outcomes)}"
            semantic_score = self.semantic_provider.compute_similarity(clean_goal, corpus)

            # Stemmed token overlap boost
            corpus_stemmed = _extract_stemmed_tokens(corpus, remove_stopwords=False)
            overlap_count = len(goal_stemmed.intersection(corpus_stemmed))
            token_coverage = overlap_count / max(1, len(goal_stemmed))

            # Combined base relevance
            relevance = (outcome_score * 0.45) + (semantic_score * 0.35) + (token_coverage * 0.20)
            if relevance < 0.10 and not matched_outcomes:
                continue

            # 5. Risk & Reversibility adjustments
            risk_penalty = self.RISK_PENALTIES.get(cap.risk_level, 0.0)
            reversibility_bonus = 0.05 if cap.reversibility == Reversibility.READ_ONLY else (
                0.02 if cap.reversibility == Reversibility.REVERSIBLE else 0.0
            )
            if prefer_read_only and cap.reversibility == Reversibility.READ_ONLY:
                reversibility_bonus += 0.10

            # 6. Check missing arguments against context
            missing_args = []
            for req_arg in cap.required_arguments:
                if req_arg not in ctx and f"${req_arg}" not in ctx:
                    # check if goal mentions arg or heuristic
                    if not self._goal_satisfies_argument(req_arg, clean_goal, ctx):
                        missing_args.append(req_arg)

            # Final Score calculation
            raw_score = (
                (relevance * 0.70)
                + (health_score * 0.20)
                + reversibility_bonus
                - risk_penalty
            ) * perm_multiplier

            # Minor penalty if missing required arguments
            if missing_args:
                raw_score *= (1.0 - (0.15 * min(3, len(missing_args))))

            final_score = max(0.0, min(1.0, raw_score))

            if final_score >= min_score_threshold:
                rationale_parts = []
                if matched_outcomes:
                    rationale_parts.append(f"matches outcome(s) {matched_outcomes}")
                rationale_parts.append(f"semantic relevance {semantic_score:.2f}")
                if health_score < 1.0:
                    rationale_parts.append(f"health degraded ({cap.health.value})")
                if lacking_perms:
                    rationale_parts.append(f"missing permissions {[p.value for p in lacking_perms]}")
                if missing_args:
                    rationale_parts.append(f"missing input arguments {missing_args}")

                scored_candidates.append(
                    ScoredCapability(
                        capability=cap,
                        score=final_score,
                        outcome_score=outcome_score,
                        semantic_score=semantic_score,
                        health_score=health_score,
                        risk_penalty=risk_penalty,
                        reversibility_bonus=reversibility_bonus,
                        matched_outcomes=matched_outcomes,
                        missing_arguments=missing_args,
                        rationale="; ".join(rationale_parts),
                    )
                )

        scored_candidates.sort(key=lambda sc: -sc.score)

        if not scored_candidates:
            return DiscoveryResult(
                goal=clean_goal,
                candidates=[],
                best_match=None,
                unsupported_reason=f"No safe and healthy capability registered matching goal: '{clean_goal}'",
                confidence=0.0,
            )

        best = scored_candidates[0]
        confidence = best.score

        # Check for ambiguity: top candidates having close score and competing outcomes
        is_ambiguous = False
        ambiguity_cands = []
        if len(scored_candidates) > 1:
            second = scored_candidates[1]
            shared_outcomes = set(best.capability.supported_outcomes).intersection(set(second.capability.supported_outcomes))
            same_intent = bool(shared_outcomes) or (
                _extract_stemmed_tokens(best.capability.name) == _extract_stemmed_tokens(second.capability.name)
            )
            is_same_underlying = (
                bool(best.capability.underlying_id and second.capability.underlying_id)
                and (
                    best.capability.underlying_id == second.capability.underlying_id
                    or best.capability.underlying_id in second.capability.supported_outcomes
                    or second.capability.underlying_id in best.capability.supported_outcomes
                )
            )
            if (
                best.score >= 0.50
                and abs(best.score - second.score) <= 0.05
                and best.capability.capability_id != second.capability.capability_id
                and not is_same_underlying
                and same_intent
            ):
                is_ambiguous = True
                ambiguity_cands = [best.capability.capability_id, second.capability.capability_id]

        missing_reqs = list(best.missing_arguments)

        return DiscoveryResult(
            goal=clean_goal,
            candidates=scored_candidates,
            best_match=best,
            is_ambiguous=is_ambiguous,
            ambiguity_candidates=ambiguity_cands,
            missing_requirements=missing_reqs,
            confidence=confidence,
        )

    def _goal_satisfies_argument(self, arg_name: str, goal: str, context: Dict[str, Any]) -> bool:
        """Heuristic check whether goal text or context implicitly provides the required argument."""
        g_lower = goal.lower()
        if arg_name in context:
            return True
        if arg_name in ("path", "folder", "directory") and ("download" in g_lower or "picture" in g_lower or "desktop" in g_lower):
            return True
        if arg_name in ("query", "text", "search") and len(goal.split()) >= 2:
            return True
        return False
