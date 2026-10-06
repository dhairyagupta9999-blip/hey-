"""Project Health Snapshot and State Assessment for Hey Jev.

Enables answering:
"Is my project still working?"
by inspecting Git cleanliness, recent test results, active processes, and warnings.
"""
from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from git_inspector import GitInspector
from project_model import Project


@dataclass
class ProjectHealthSnapshot:
    """Consolidated operational health status of a project."""
    project_id: str
    repository_healthy: bool = True
    working_tree_clean: bool = True
    last_commit: str = ""
    last_commit_message: str = ""
    recent_failures: List[str] = field(default_factory=list)
    last_test_result: str = "UNKNOWN"  # "PASSED", "FAILED", "UNKNOWN"
    active_processes: List[Dict[str, Any]] = field(default_factory=list)
    known_build_status: str = "HEALTHY"  # "HEALTHY", "BROKEN", "UNKNOWN"
    last_activity: float = field(default_factory=time.time)
    warnings: List[str] = field(default_factory=list)

    @property
    def is_working(self) -> bool:
        """Overall determination of whether project is in a working state."""
        return (
            self.repository_healthy
            and self.known_build_status != "BROKEN"
            and self.last_test_result != "FAILED"
            and len(self.recent_failures) == 0
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ProjectHealthSnapshot:
        return cls(**data)


class ProjectHealthAssessor:
    """Assesses current health state of a project."""

    def __init__(self, git_inspector: Optional[GitInspector] = None) -> None:
        self.git_inspector = git_inspector or GitInspector()

    def assess(
        self,
        project: Project,
        last_test_result: Optional[str] = None,
        recent_failures: Optional[List[str]] = None,
        active_processes: Optional[List[Dict[str, Any]]] = None,
    ) -> ProjectHealthSnapshot:
        """Inspect repository and project indicators to generate a health snapshot."""
        warnings: List[str] = []
        git_info = self.git_inspector.inspect(project.root_path)

        is_git = git_info.get("is_git", False)
        repo_healthy = is_git or bool(project.root_path and os.path.exists(project.root_path))
        clean = git_info.get("clean", True)
        last_commit = git_info.get("commit", "")
        commit_msg = git_info.get("commit_message", "")

        if not clean:
            warnings.append(f"Working tree has {git_info.get('modified_count', 0)} uncommitted changes.")

        test_res = last_test_result or "UNKNOWN"
        if test_res == "FAILED":
            warnings.append("Latest test execution reported failures.")

        failures = list(recent_failures or [])
        if failures:
            warnings.append(f"{len(failures)} unresolved failure(s) recorded.")

        build_status = "BROKEN" if test_res == "FAILED" or len(failures) > 0 else "HEALTHY"

        return ProjectHealthSnapshot(
            project_id=project.project_id,
            repository_healthy=repo_healthy,
            working_tree_clean=clean,
            last_commit=last_commit,
            last_commit_message=commit_msg,
            recent_failures=failures,
            last_test_result=test_res,
            active_processes=list(active_processes or []),
            known_build_status=build_status,
            last_activity=time.time(),
            warnings=warnings,
        )
