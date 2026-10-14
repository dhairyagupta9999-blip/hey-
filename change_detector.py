"""Change Detection Engine for Hey Jev Background Automation.

Compares:
- Previous vs current research results (claims, sources, findings)
- Previous vs current project health & test states
- Previous vs current filesystem / git observations

Identifies: NEW, REMOVED, MODIFIED, UNCHANGED.
"""
from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Union


class ChangeType(str, Enum):
    """Classification of state delta."""
    NEW = "NEW"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    UNCHANGED = "UNCHANGED"


@dataclass
class ChangeReport:
    """Structured delta analysis comparing prior state to current state."""
    change_type: ChangeType = ChangeType.UNCHANGED
    has_changes: bool = False
    added: List[Any] = field(default_factory=list)
    removed: List[Any] = field(default_factory=list)
    modified: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = "No changes detected."
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "change_type": self.change_type.value,
            "has_changes": self.has_changes,
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "summary": self.summary,
            "details": self.details,
        }


class ChangeDetector:
    """Detects meaningful semantic and operational changes across observations."""

    def compare_dicts(
        self,
        previous: Optional[Dict[str, Any]],
        current: Optional[Dict[str, Any]],
    ) -> ChangeReport:
        """Compare two dictionary snapshots."""
        prev = previous or {}
        curr = current or {}

        if not prev and curr:
            return ChangeReport(
                change_type=ChangeType.NEW,
                has_changes=True,
                added=list(curr.keys()),
                summary=f"New state initialized with {len(curr)} keys.",
                details={"new_keys": list(curr.keys())},
            )

        if prev and not curr:
            return ChangeReport(
                change_type=ChangeType.REMOVED,
                has_changes=True,
                removed=list(prev.keys()),
                summary=f"Previous state removed ({len(prev)} keys).",
                details={"removed_keys": list(prev.keys())},
            )

        prev_keys: Set[str] = set(prev.keys())
        curr_keys: Set[str] = set(curr.keys())

        added_keys = sorted(curr_keys - prev_keys)
        removed_keys = sorted(prev_keys - curr_keys)
        modified_items: List[Dict[str, Any]] = []

        for common_key in sorted(prev_keys & curr_keys):
            if prev[common_key] != curr[common_key]:
                modified_items.append({
                    "key": common_key,
                    "old_value": prev[common_key],
                    "new_value": curr[common_key],
                })

        has_changes = bool(added_keys or removed_keys or modified_items)
        if not has_changes:
            return ChangeReport(change_type=ChangeType.UNCHANGED, has_changes=False, summary="State unchanged.")

        summary_parts = []
        if added_keys:
            summary_parts.append(f"{len(added_keys)} added")
        if removed_keys:
            summary_parts.append(f"{len(removed_keys)} removed")
        if modified_items:
            summary_parts.append(f"{len(modified_items)} modified")

        return ChangeReport(
            change_type=ChangeType.MODIFIED,
            has_changes=True,
            added=added_keys,
            removed=removed_keys,
            modified=modified_items,
            summary=", ".join(summary_parts) + ".",
            details={"modified_keys": [m["key"] for m in modified_items]},
        )

    def compare_lists(
        self,
        previous: Optional[List[Any]],
        current: Optional[List[Any]],
    ) -> ChangeReport:
        """Compare two collections (e.g. lists of sources, errors, commits)."""
        prev = previous or []
        curr = current or []

        prev_set = set(str(x) for x in prev)
        curr_set = set(str(x) for x in curr)

        added = sorted(curr_set - prev_set)
        removed = sorted(prev_set - curr_set)

        has_changes = bool(added or removed)
        if not has_changes:
            return ChangeReport(change_type=ChangeType.UNCHANGED, has_changes=False, summary="Collection unchanged.")

        summary_parts = []
        if added:
            summary_parts.append(f"{len(added)} new items")
        if removed:
            summary_parts.append(f"{len(removed)} removed items")

        c_type = ChangeType.NEW if (not prev and curr) else (ChangeType.REMOVED if (prev and not curr) else ChangeType.MODIFIED)

        return ChangeReport(
            change_type=c_type,
            has_changes=True,
            added=added,
            removed=removed,
            summary=", ".join(summary_parts) + ".",
        )

    def compare_text(
        self,
        previous_text: str,
        current_text: str,
    ) -> ChangeReport:
        """Compare textual summaries or documentation with line diffing."""
        if previous_text == current_text:
            return ChangeReport(change_type=ChangeType.UNCHANGED, has_changes=False, summary="Text unchanged.")

        if not previous_text.strip() and current_text.strip():
            return ChangeReport(change_type=ChangeType.NEW, has_changes=True, summary="New content created.")

        if previous_text.strip() and not current_text.strip():
            return ChangeReport(change_type=ChangeType.REMOVED, has_changes=True, summary="Content cleared.")

        diff = list(difflib.unified_diff(
            previous_text.splitlines(),
            current_text.splitlines(),
            lineterm="",
        ))

        return ChangeReport(
            change_type=ChangeType.MODIFIED,
            has_changes=True,
            summary=f"Text modified ({len(diff)} diff lines).",
            details={"diff_lines": diff[:30]},
        )

    def diff_dicts(self, previous: Optional[Dict[str, Any]], current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Convenience method returning diff dictionary."""
        report = self.compare_dicts(previous, current)
        d = report.to_dict()
        d["modified_items"] = report.modified
        d["modified"] = [m["key"] for m in report.modified]
        d["additions_count"] = len(report.added)
        d["removals_count"] = len(report.removed)
        d["modifications_count"] = len(report.modified)
        return d

    def diff_lists(self, previous: Optional[List[Any]], current: Optional[List[Any]]) -> Dict[str, Any]:
        """Convenience method returning diff dictionary."""
        report = self.compare_lists(previous, current)
        d = report.to_dict()
        d["additions_count"] = len(report.added)
        d["removals_count"] = len(report.removed)
        return d

    def diff_text(self, previous_text: str, current_text: str) -> Dict[str, Any]:
        """Convenience method returning text diff dictionary."""
        report = self.compare_text(previous_text, current_text)
        d = report.to_dict()
        diff_lines = report.details.get("diff_lines", [])
        additions = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removals = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
        d["additions_count"] = additions if additions > 0 else (1 if report.has_changes else 0)
        d["removals_count"] = removals
        d["diff_lines"] = diff_lines
        return d
