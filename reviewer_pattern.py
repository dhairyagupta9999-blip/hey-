"""Reviewer Pattern and Closed-Loop Quality Gating for Hey Jev.

Coordinates iterative peer-review loops between Worker agents (e.g. Antigravity)
and Reviewer agents (e.g. Claude) with bounded review rounds (default max 2 rounds).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_message import AgentMessage, AgentMessageType
from agent_result import AgentResult, AgentResultStatus
from agent_task import AgentTask


class ReviewStatus(str, Enum):
    """Verdict emitted by an independent reviewing agent."""
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    REJECTED = "REJECTED"


@dataclass
class ReviewFeedback:
    """Structured review assessment emitted by a reviewer agent."""
    status: ReviewStatus
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    summary: str = ""
    round_number: int = 1
    reviewer_agent: str = "claude_reviewer"
    reviewed_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class ReviewLoopController:
    """Governs bounded worker-reviewer iterations to enforce quality gates."""

    def __init__(self, max_review_rounds: int = 2) -> None:
        self.max_review_rounds = max_review_rounds
        self.review_history: List[ReviewFeedback] = []

    def execute_review_round(
        self,
        task: AgentTask,
        worker_agent: str,
        reviewer_agent: str,
        worker_fn: Callable[[AgentTask, Optional[ReviewFeedback]], AgentResult],
        reviewer_fn: Callable[[AgentTask, AgentResult, int], ReviewFeedback],
    ) -> Tuple[AgentResult, List[ReviewFeedback]]:
        """Run bounded worker-reviewer iterations until APPROVED or max rounds exceeded."""
        current_feedback: Optional[ReviewFeedback] = None
        last_result: Optional[AgentResult] = None

        for round_num in range(1, self.max_review_rounds + 1):
            # 1. Worker implements / updates task
            worker_result = worker_fn(task, current_feedback)
            last_result = worker_result

            # 2. Reviewer inspects output
            feedback = reviewer_fn(task, worker_result, round_num)
            self.review_history.append(feedback)
            current_feedback = feedback

            # 3. Check for approval
            if feedback.status == ReviewStatus.APPROVED:
                # Merge reviewer endorsement into result
                worker_result.recommendations.append(f"Approved by {reviewer_agent} in round {round_num}")
                return worker_result, self.review_history

            # If rejected with fatal objection, terminate early
            if feedback.status == ReviewStatus.REJECTED:
                worker_result.status = AgentResultStatus.FAILURE
                worker_result.error = f"Rejected by {reviewer_agent}: {feedback.summary}"
                return worker_result, self.review_history

        # If max rounds reached without approval
        if last_result:
            last_result.summary += f" (Review concluded after {self.max_review_rounds} rounds with {len(current_feedback.issues if current_feedback else [])} open items)"
        return last_result or AgentResult(status=AgentResultStatus.FAILURE), self.review_history
