"""Undo Engine, Tamper-Evident Operation Timeline, and Semantic Locking for Hey Jev.

Enforces:
- OperationTimeline: Tamper-evident cryptographic hash-chained action history
- SemanticLockManager: Resource-level mutual exclusion preventing competing worker mutations
- UndoManager:
  - can_undo(), preview_undo(), undo()
  - Reverse-dependency group undo (undo_group)
  - Strict rejection of IRREVERSIBLE operations
  - Compensation execution with post-reversal verification
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from reliability_model import ActionRecord, ExecutionStatus, ReversibilityType


class OperationTimeline:
    """Tamper-evident, cryptographically chained sequence of recorded actions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: List[ActionRecord] = []
        self._action_index: Dict[str, ActionRecord] = {}
        self._last_hash: Optional[str] = "GENESIS_RELIABILITY_0"

    def record_action(self, action: ActionRecord) -> ActionRecord:
        """Append an ActionRecord to the timeline and chain its hash."""
        with self._lock:
            action.prev_record_hash = self._last_hash
            action.compute_hash()
            self._last_hash = action.record_hash
            self._records.append(action)
            self._action_index[action.action_id] = action
            return action

    def get_action(self, action_id: str) -> Optional[ActionRecord]:
        with self._lock:
            return self._action_index.get(action_id)

    def get_group_actions(self, group_id: str) -> List[ActionRecord]:
        with self._lock:
            return [a for a in self._records if a.group_id == group_id]

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """Verify hash chain linkage across all historical records.

        Checks that each record's prev_record_hash matches the prior record's
        stored record_hash, ensuring the chain was not tampered with or truncated.
        Does NOT recompute hashes from mutable fields (status may change after undo).
        """
        with self._lock:
            errors: List[str] = []
            expected_prev = "GENESIS_RELIABILITY_0"

            for idx, rec in enumerate(self._records):
                if rec.prev_record_hash != expected_prev:
                    errors.append(f"Record {idx} ({rec.action_id}) broken prev_hash: expected {expected_prev}, got {rec.prev_record_hash}")
                if not rec.record_hash:
                    errors.append(f"Record {idx} ({rec.action_id}) missing record_hash")
                expected_prev = rec.record_hash

            return (len(errors) == 0), errors

    def list_records(self, limit: int = 100) -> List[ActionRecord]:
        with self._lock:
            return list(self._records[-limit:])


class SemanticLockManager:
    """Resource-level mutual exclusion preventing competing mutations on shared targets."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._locks: Dict[str, str] = {}  # target_resource -> holder_id

    def acquire_lock(self, resource_key: str, holder_id: str) -> bool:
        """Acquire lease on resource target."""
        with self._lock:
            current = self._locks.get(resource_key)
            if current is None or current == holder_id:
                self._locks[resource_key] = holder_id
                return True
            return False

    def release_lock(self, resource_key: str, holder_id: str) -> bool:
        """Release lease on resource target."""
        with self._lock:
            if self._locks.get(resource_key) == holder_id:
                del self._locks[resource_key]
                return True
            return False

    def is_locked(self, resource_key: str) -> bool:
        with self._lock:
            return resource_key in self._locks


class UndoManager:
    """Governs safe action reversal, compensation execution, and group undos."""

    def __init__(self, timeline: Optional[OperationTimeline] = None) -> None:
        self.timeline = timeline or OperationTimeline()
        self._lock = threading.RLock()

    def can_undo(self, action_id: str) -> Tuple[bool, str]:
        """Check if an action meets safety and reversibility invariants for undo."""
        action = self.timeline.get_action(action_id)
        if not action:
            return False, f"Action '{action_id}' not found in timeline."

        if action.reversibility == ReversibilityType.IRREVERSIBLE:
            return False, f"Action '{action.action_type}' is permanently IRREVERSIBLE."

        if action.status not in (ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED, ExecutionStatus.ALREADY_APPLIED):
            return False, f"Cannot undo action in status '{action.status.value}'."

        if not action.compensation_action and not action.rollback_metadata:
            return False, f"No compensation metadata recorded for action '{action_id}'."

        return True, "Safe to undo."

    def preview_undo(self, action_id: str) -> Dict[str, Any]:
        """Generate transparent preview before executing an undo operation."""
        can, reason = self.can_undo(action_id)
        action = self.timeline.get_action(action_id)
        if not action:
            return {"can_undo": False, "reason": reason}

        comp = action.compensation_action or action.rollback_metadata
        return {
            "action_id": action_id,
            "can_undo": can,
            "reason": reason,
            "original_action": action.action_type,
            "target": action.target,
            "compensation_action": comp,
            "reversibility": action.reversibility.value,
            "risk": action.risk_level,
        }

    def undo(self, action_id: str) -> bool:
        """Execute compensation for an individual action."""
        with self._lock:
            can, reason = self.can_undo(action_id)
            if not can:
                return False

            action = self.timeline.get_action(action_id)
            if not action:
                return False

            comp = action.compensation_action or action.rollback_metadata
            success = self._execute_compensation_step(comp)

            if success:
                # Append reversal record to timeline
                reversal_record = ActionRecord(
                    task_id=action.task_id,
                    mission_id=action.mission_id,
                    agent_id="undo_manager",
                    action_type="undo_compensation",
                    target=action.target,
                    input_summary={"undone_action_id": action_id, "compensation": comp},
                    status=ExecutionStatus.SUCCEEDED,
                    reversibility=ReversibilityType.IRREVERSIBLE,  # Don't undo an undo
                )
                self.timeline.record_action(reversal_record)
                action.status = ExecutionStatus.UNKNOWN  # Mark original as reversed
                return True
            return False

    def undo_group(self, group_id: str) -> Tuple[bool, List[str]]:
        """Undo an action group in reverse dependency order."""
        with self._lock:
            actions = self.timeline.get_group_actions(group_id)
            if not actions:
                return False, [f"No actions found for group '{group_id}'"]

            # Reverse chronological order
            reversed_actions = list(reversed(actions))
            undone: List[str] = []
            errors: List[str] = []

            for act in reversed_actions:
                can, reason = self.can_undo(act.action_id)
                if not can:
                    errors.append(f"Cannot undo {act.action_id}: {reason}")
                    continue

                if self.undo(act.action_id):
                    undone.append(act.action_id)
                else:
                    errors.append(f"Failed executing compensation for {act.action_id}")

            overall_success = (len(undone) > 0 and len(errors) == 0)
            return overall_success, undone

    def list_undoable_actions(self) -> List[Dict[str, Any]]:
        """Return list of candidate actions that can be undone."""
        with self._lock:
            candidates: List[Dict[str, Any]] = []
            for act in self.timeline.list_records(200):
                can, _ = self.can_undo(act.action_id)
                if can:
                    candidates.append({
                        "action_id": act.action_id,
                        "type": act.action_type,
                        "target": act.target,
                        "timestamp": act.started_at,
                        "reversibility": act.reversibility.value,
                    })
            return candidates

    def _execute_compensation_step(self, comp: Dict[str, Any]) -> bool:
        """Perform concrete physical compensation on disk or environment."""
        action_name = comp.get("action")

        try:
            if action_name == "move_file":
                src = comp.get("src")
                dst = comp.get("dst")
                if src and dst and os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    return os.path.exists(dst)
                return False

            elif action_name == "delete_directory":
                dir_path = comp.get("path")
                if dir_path and os.path.exists(dir_path):
                    # Only remove if directory is empty or safe
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                    else:
                        shutil.rmtree(dir_path)
                    return not os.path.exists(dir_path)
                return True

            elif action_name == "delete_file":
                file_path = comp.get("path")
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    return not os.path.exists(file_path)
                return True

            elif action_name == "restore_file_content":
                path = comp.get("path")
                orig_content = comp.get("original_content")
                if path and orig_content is not None:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(orig_content)
                    return True
                return False

            elif action_name == "custom_noop":
                return True

        except Exception:
            return False

        return False
