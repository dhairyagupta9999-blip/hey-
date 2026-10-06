"""Synthesis Engine and Multi-Opinion Adjudication for Hey Jev.

Combines outputs from multiple specialist agents, detects contradictions,
weighs confidence and evidence, and deterministically resolves disagreements.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent_result import AgentResult, AgentResultStatus


@dataclass
class SynthesizedResolution:
    """Consolidated outcome of multi-agent synthesis or debate."""
    selected_action: str
    winning_recommendation: str
    confidence: float
    rationale: str
    accepted_positions: List[Dict[str, Any]] = field(default_factory=list)
    rejected_positions: List[Dict[str, Any]] = field(default_factory=list)
    contradictions_detected: List[Dict[str, Any]] = field(default_factory=list)
    supporting_evidence: List[Any] = field(default_factory=list)
    synthesized_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Adjudicator(ABC):
    """Abstract interface for multi-agent disagreement resolution."""

    @abstractmethod
    def adjudicate(self, positions: List[Dict[str, Any]]) -> SynthesizedResolution:
        """Resolve conflicting agent positions into an authoritative recommendation."""
        pass


class DeterministicAdjudicator(Adjudicator):
    """Evidence-weighted adjudicator prioritizing platform compatibility and confidence."""

    def adjudicate(self, positions: List[Dict[str, Any]]) -> SynthesizedResolution:
        if not positions:
            return SynthesizedResolution(
                selected_action="none",
                winning_recommendation="No positions provided.",
                confidence=0.0,
                rationale="Empty position set.",
            )

        if len(positions) == 1:
            p = positions[0]
            return SynthesizedResolution(
                selected_action=p.get("action", p.get("recommendation", "proceed")),
                winning_recommendation=p.get("recommendation", ""),
                confidence=float(p.get("confidence", 0.9)),
                rationale=f"Single consensus position from {p.get('agent_name', 'agent')}",
                accepted_positions=[p],
                supporting_evidence=p.get("evidence", []),
            )

        # 1. Contradiction Detection
        contradictions: List[Dict[str, Any]] = []
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                p1, p2 = positions[i], positions[j]
                rec1 = str(p1.get("recommendation", "")).strip().lower()
                rec2 = str(p2.get("recommendation", "")).strip().lower()
                if rec1 and rec2 and rec1 != rec2:
                    contradictions.append({
                        "agent_a": p1.get("agent_name"),
                        "rec_a": p1.get("recommendation"),
                        "agent_b": p2.get("agent_name"),
                        "rec_b": p2.get("recommendation"),
                    })

        # 2. Score candidates by evidence depth, confidence, and safety/compatibility markers
        scored: List[Tuple[float, Dict[str, Any], str]] = []
        for p in positions:
            score = float(p.get("confidence", 0.8))
            evidence_count = len(p.get("evidence", []))
            score += min(0.15, evidence_count * 0.05)

            rec_lower = str(p.get("recommendation", "")).lower()
            text_lower = f"{rec_lower} {str(p.get('rationale', '')).lower()}"

            # Reward verified compatibility markers
            if "windows 10" in text_lower or "compatible" in text_lower or "tested" in text_lower:
                score += 0.10

            # Penalize known platform incompatibilities
            if "fails on windows" in text_lower or "incompatible" in text_lower or "broken" in text_lower:
                score -= 0.50

            scored.append((score, p, p.get("agent_name", "unknown")))

        scored.sort(key=lambda s: -s[0])

        best_score, winner, winner_name = scored[0]
        accepted = [winner]
        rejected = [s[1] for s in scored[1:]]

        rationale = (
            f"Selected position from {winner_name} (score: {best_score:.2f}) "
            f"supported by {len(winner.get('evidence', []))} evidence items. "
            f"Overruled {len(rejected)} competing positions."
        )

        all_evidence = []
        for p in positions:
            all_evidence.extend(p.get("evidence", []))

        return SynthesizedResolution(
            selected_action=winner.get("action", winner.get("recommendation", "proceed")),
            winning_recommendation=winner.get("recommendation", ""),
            confidence=min(1.0, max(0.0, best_score)),
            rationale=rationale,
            accepted_positions=accepted,
            rejected_positions=rejected,
            contradictions_detected=contradictions,
            supporting_evidence=all_evidence,
        )


class SynthesisEngine:
    """Orchestrates multi-agent result aggregation and adjudication."""

    def __init__(self, adjudicator: Optional[Adjudicator] = None) -> None:
        self.adjudicator = adjudicator or DeterministicAdjudicator()

    def synthesize_results(self, agent_results: Dict[str, AgentResult]) -> SynthesizedResolution:
        """Combine multiple AgentResults into a single SynthesizedResolution."""
        positions: List[Dict[str, Any]] = []

        for agent_name, res in agent_results.items():
            primary_rec = res.recommendations[0] if res.recommendations else res.summary
            positions.append({
                "agent_name": agent_name,
                "action": res.next_action or primary_rec,
                "recommendation": primary_rec,
                "confidence": res.confidence,
                "evidence": res.evidence,
                "artifacts": res.artifacts,
                "status": res.status.value,
                "rationale": res.summary,
            })

        return self.adjudicator.adjudicate(positions)
