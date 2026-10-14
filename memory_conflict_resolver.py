"""Evidence-Based Memory Conflict Resolver for Hey Jev.

Resolves contradictory memories by evaluating real-world system evidence:
- Filesystem existence checks
- Git repository validity checks
- Observation recency and epistemic priority
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from git_inspector import GitInspector
from memory_model import EpistemicStatus, MemoryRecord, MemoryStatus
from memory_store import MemoryStore


class MemoryConflictResolver:
    """Discovers and resolves contradictions across stored memories."""

    def __init__(
        self,
        store: MemoryStore,
        git_inspector: Optional[GitInspector] = None,
    ) -> None:
        self.store = store
        self.git_inspector = git_inspector or GitInspector()

    def resolve_path_conflict(
        self,
        memory_a: MemoryRecord,
        memory_b: MemoryRecord,
        path_a: str,
        path_b: str,
    ) -> Tuple[MemoryRecord, MemoryRecord, str]:
        """Resolve conflicting paths by checking filesystem existence and Git validity.

        Returns (winner_record, loser_record, explanation).
        """
        exists_a = os.path.exists(path_a)
        exists_b = os.path.exists(path_b)
        git_a = self.git_inspector.is_git_repository(path_a) if exists_a else False
        git_b = self.git_inspector.is_git_repository(path_b) if exists_b else False

        if git_b and not git_a:
            winner, loser = memory_b, memory_a
            winner_path, loser_path = path_b, path_a
            reason = f"Path '{path_b}' is a verified git repository while '{path_a}' is not."
        elif git_a and not git_b:
            winner, loser = memory_a, memory_b
            winner_path, loser_path = path_a, path_b
            reason = f"Path '{path_a}' is a verified git repository while '{path_b}' is not."
        elif exists_b and not exists_a:
            winner, loser = memory_b, memory_a
            winner_path, loser_path = path_b, path_a
            reason = f"Path '{path_b}' exists on disk while '{path_a}' does not."
        elif exists_a and not exists_b:
            winner, loser = memory_a, memory_b
            winner_path, loser_path = path_a, path_b
            reason = f"Path '{path_a}' exists on disk while '{path_b}' does not."
        else:
            # Tie breaker: recency
            if memory_b.created_at >= memory_a.created_at:
                winner, loser = memory_b, memory_a
                winner_path, loser_path = path_b, path_a
                reason = f"Path '{path_b}' was observed more recently."
            else:
                winner, loser = memory_a, memory_b
                winner_path, loser_path = path_a, path_b
                reason = f"Path '{path_a}' was observed more recently."

        # Apply supersession in store
        winner.confidence = 1.0
        winner.epistemic_status = EpistemicStatus.FACT
        winner.metadata["conflict_resolution"] = reason
        self.store.update(winner)

        loser.metadata["superseded_reason"] = reason
        self.store.supersede(loser.memory_id, winner)

        return winner, loser, reason
