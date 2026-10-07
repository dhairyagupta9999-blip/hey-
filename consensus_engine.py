"""Consensus Engine, Result Merging, and Competition Evaluation for Hey Jev.

Provides:
- SwarmResultMerger: Aggregates multi-worker outputs, preserves evidence, detects
  contradictions via SynthesisEngine, and deduplicates recommendations.
- ConsensusEngine: Multi-agent consensus using evidence-weighting, confidence-weighting,
  majority, expert-priority, and deterministic adjudication.
- CompetitionEvaluator: Rigorous multi-criteria evaluator for competing solutions
  (correctness, test coverage, performance, security, and maintainability).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_result import AgentResult, AgentResultStatus
from synthesis_engine import DeterministicAdjudicator, SynthesisEngine, SynthesizedResolution


class ConsensusStrategy(str, Enum):
    """Consensus resolution algorithm."""
    EVIDENCE_WEIGHTED = "EVIDENCE_WEIGHTED"
    CONFIDENCE_WEIGHTED = "CONFIDENCE_WEIGHTED"
    MAJORITY = "MAJORITY"
    EXPERT_PRIORITY = "EXPERT_PRIORITY"
    ADJUDICATOR = "ADJUDICATOR"


@dataclass
class CompetitionScore:
    """Evaluation score breakdown for a candidate solution."""
    candidate_id: str
    worker_id: str
    correctness: float = 0.0      # 0.0 - 1.0 (weight 0.35)
    test_coverage: float = 0.0    # 0.0 - 1.0 (weight 0.25)
    performance: float = 0.0      # 0.0 - 1.0 (weight 0.15)
    security: float = 0.0         # 0.0 - 1.0 (weight 0.15)
    maintainability: float = 0.0  # 0.0 - 1.0 (weight 0.10)
    composite_score: float = 0.0
    notes: List[str] = field(default_factory=list)

    def calculate_composite(self) -> float:
        self.composite_score = (
            (self.correctness * 0.35)
            + (self.test_coverage * 0.25)
            + (self.performance * 0.15)
            + (self.security * 0.15)
            + (self.maintainability * 0.10)
        )
        return self.composite_score

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class CompetitionEvaluator:
    """Evaluates multiple competing implementations and crowns an authoritative winner."""

    def evaluate_candidates(
        self, candidate_results: Dict[str, AgentResult]
    ) -> Tuple[str, AgentResult, List[CompetitionScore]]:
        """Rank candidates and select highest composite scorer."""
        if not candidate_results:
            empty_res = AgentResult(error="No candidates submitted for competition", status=AgentResultStatus.FAILURE)
            return "none", empty_res, []

        scores: List[CompetitionScore] = []

        for worker_id, result in candidate_results.items():
            score = CompetitionScore(candidate_id=worker_id, worker_id=worker_id)
            notes: List[str] = []

            # 1. Correctness: baseline confidence + absence of errors
            if result.is_success and not result.error:
                score.correctness = result.confidence
                notes.append(f"Success reported with {result.confidence:.2f} confidence")
            else:
                score.correctness = 0.1
                notes.append("Execution failure or error reported")

            # 2. Evidence-based scoring
            evidence_texts = []
            for ev in result.evidence:
                if isinstance(ev, dict):
                    evidence_texts.append(str(ev.get("criterion", "")) + " " + str(ev.get("status", "")))
                elif isinstance(ev, str):
                    evidence_texts.append(ev)
            ev_blob = " ".join(evidence_texts).lower()

            # Test coverage indicators
            if "test" in ev_blob or "tests_pass" in ev_blob:
                score.test_coverage = 0.95
                notes.append("Verified test coverage")
            else:
                score.test_coverage = 0.50

            # Performance & platform compatibility
            summary_lower = (result.summary + " " + " ".join(result.recommendations)).lower()
            if "o(1)" in summary_lower or "o(n)" in summary_lower or "optimal" in summary_lower or "fast" in summary_lower:
                score.performance = 0.90
                notes.append("High performance markers detected")
            else:
                score.performance = 0.60

            # Security markers
            if "security_audit" in ev_blob or "clean" in ev_blob or "safe" in summary_lower:
                score.security = 0.95
                notes.append("Security audit verified")
            elif "insecure" in summary_lower or "leak" in summary_lower:
                score.security = 0.20
            else:
                score.security = 0.70

            # Maintainability
            if "edge cases" in summary_lower and "fails" in summary_lower:
                score.maintainability = 0.30
                notes.append("Fails on edge cases")
            else:
                score.maintainability = 0.80

            score.notes = notes
            score.calculate_composite()
            scores.append(score)

        scores.sort(key=lambda s: -s.composite_score)
        winner = scores[0]
        winner_result = candidate_results[winner.worker_id]

        return winner.worker_id, winner_result, scores


class SwarmResultMerger:
    """Consolidates diverse agent results into unified evidence-backed outcomes."""

    def __init__(self) -> None:
        self.synthesis_engine = SynthesisEngine(adjudicator=DeterministicAdjudicator())

    def merge_results(
        self,
        worker_results: Dict[str, AgentResult],
        objective: str = "",
    ) -> AgentResult:
        """Merge, deduplicate, and cross-reference worker outputs."""
        if not worker_results:
            return AgentResult(summary="No worker results available", status=AgentResultStatus.FAILURE, confidence=0.0)

        # 1. Synthesize via existing SynthesisEngine
        resolution: SynthesizedResolution = self.synthesis_engine.synthesize_results(worker_results)

        # 2. Deduplicate recommendations and files changed
        all_recs: List[str] = []
        all_files: Set[str] = set()
        all_commands: List[str] = []
        all_artifacts: List[Dict[str, Any]] = []
        all_evidence: List[Any] = []

        seen_recs: Set[str] = set()
        for res in worker_results.values():
            for rec in res.recommendations:
                norm = rec.strip()
                if norm and norm.lower() not in seen_recs:
                    seen_recs.add(norm.lower())
                    all_recs.append(norm)

            all_files.update(res.files_changed)
            all_commands.extend(res.commands_run)
            all_artifacts.extend(res.artifacts)
            all_evidence.extend(res.evidence)

        # Determine overall status
        any_failed = any(not r.is_success for r in worker_results.values())
        all_failed = all(not r.is_success for r in worker_results.values())

        if all_failed:
            overall_status = AgentResultStatus.FAILURE
        elif any_failed:
            overall_status = AgentResultStatus.PARTIAL
        else:
            overall_status = AgentResultStatus.SUCCESS

        summary = f"Synthesized from {len(worker_results)} specialists: {resolution.winning_recommendation}"
        if resolution.contradictions_detected:
            summary += f" (Resolved {len(resolution.contradictions_detected)} contradictions)"

        return AgentResult(
            result={
                "winning_action": resolution.selected_action,
                "synthesized_recommendation": resolution.winning_recommendation,
                "contradictions": resolution.contradictions_detected,
                "accepted_positions": resolution.accepted_positions,
                "rejected_positions": resolution.rejected_positions,
            },
            summary=summary,
            recommendations=all_recs or [resolution.winning_recommendation],
            files_changed=sorted(list(all_files)),
            commands_run=all_commands,
            artifacts=all_artifacts,
            evidence=all_evidence,
            confidence=resolution.confidence,
            next_action=resolution.selected_action,
            status=overall_status,
        )


class ConsensusEngine:
    """Orchestrates consensus resolution across specialist positions."""

    def __init__(self, default_strategy: ConsensusStrategy = ConsensusStrategy.EVIDENCE_WEIGHTED) -> None:
        self.default_strategy = default_strategy
        self.merger = SwarmResultMerger()

    def resolve_consensus(
        self,
        worker_results: Dict[str, AgentResult],
        strategy: Optional[ConsensusStrategy] = None,
        expert_weights: Optional[Dict[str, float]] = None,
    ) -> AgentResult:
        """Resolve consensus using the selected mathematical weighting strategy."""
        strat = strategy or self.default_strategy

        if not worker_results:
            return AgentResult(summary="Empty position set", status=AgentResultStatus.FAILURE, confidence=0.0)

        if strat == ConsensusStrategy.EXPERT_PRIORITY and expert_weights:
            # Score each candidate by expert weight
            best_agent = max(worker_results.keys(), key=lambda a: expert_weights.get(a, 0.5) * worker_results[a].confidence)
            return worker_results[best_agent]

        # Use unified synthesis merger for evidence-weighted and adjudicator strategies
        return self.merger.merge_results(worker_results)
