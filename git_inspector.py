"""Safe Read-Only Git Repository Inspector for Hey Jev.

Enforces:
- 100% read-only Git inspection (no checkout, commit, push, pull, reset, or branch switching)
- Fast subprocess execution bounded by short timeouts (3.0s)
- Graceful degradation when git is unavailable or directory is not a git repo
"""
from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional, Tuple


class GitInspector:
    """Safe read-only inspector for local git repositories."""

    def __init__(self, timeout_sec: float = 3.0) -> None:
        self.timeout = timeout_sec

    def _run_git(self, repo_path: str, args: List[str]) -> Tuple[int, str]:
        """Execute read-only git command in target repo directory."""
        if not os.path.isdir(repo_path):
            return 1, ""
        try:
            cmd = ["git"] + args
            res = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return res.returncode, res.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            return 1, ""

    def is_git_repository(self, path: str) -> bool:
        """Check if target path is inside a working git repository."""
        if not path or not os.path.isdir(path):
            return False
        if os.path.exists(os.path.join(path, ".git")):
            return True
        code, out = self._run_git(path, ["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out.lower() == "true"

    def get_branch(self, repo_path: str) -> str:
        """Get the active branch name or detached HEAD indicator."""
        code, out = self._run_git(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
        return out if code == 0 else ""

    def get_latest_commit(self, repo_path: str) -> Dict[str, str]:
        """Get latest commit metadata: hash, author, subject, timestamp."""
        format_str = "%H%x00%an%x00%s%x00%ct"
        code, out = self._run_git(repo_path, ["log", "-1", f"--format={format_str}"])
        if code != 0 or not out:
            return {"hash": "", "author": "", "message": "", "timestamp": ""}
        parts = out.split("\x00")
        return {
            "hash": parts[0] if len(parts) > 0 else "",
            "author": parts[1] if len(parts) > 1 else "",
            "message": parts[2] if len(parts) > 2 else "",
            "timestamp": parts[3] if len(parts) > 3 else "",
        }

    def get_recent_commits(self, repo_path: str, limit: int = 5) -> List[Dict[str, str]]:
        """Get recent N commit summaries."""
        format_str = "%h - %s (%an)"
        code, out = self._run_git(repo_path, ["log", f"-{limit}", f"--format={format_str}"])
        if code != 0 or not out:
            return []
        return [{"summary": line} for line in out.splitlines() if line.strip()]

    def get_status(self, repo_path: str) -> Dict[str, Any]:
        """Inspect working tree cleanliness without modifying state."""
        code, out = self._run_git(repo_path, ["status", "--porcelain"])
        if code != 0:
            return {"healthy": False, "clean": False, "modified": [], "untracked": []}

        lines = [line.strip() for line in out.splitlines() if line.strip()]
        modified = []
        untracked = []
        for line in lines:
            if line.startswith("??"):
                untracked.append(line[3:])
            else:
                modified.append(line[3:])

        return {
            "healthy": True,
            "clean": len(lines) == 0,
            "modified_count": len(modified),
            "untracked_count": len(untracked),
            "modified_files": modified[:20],
            "untracked_files": untracked[:20],
        }

    def inspect(self, repo_path: str) -> Dict[str, Any]:
        """Produce full read-only Git status report."""
        if not self.is_git_repository(repo_path):
            return {
                "is_git": False,
                "branch": "",
                "commit": "",
                "commit_message": "",
                "author": "",
                "clean": True,
                "modified_count": 0,
                "untracked_count": 0,
                "recent_commits": [],
            }

        branch = self.get_branch(repo_path)
        commit = self.get_latest_commit(repo_path)
        status = self.get_status(repo_path)
        recent = self.get_recent_commits(repo_path, limit=3)

        return {
            "is_git": True,
            "branch": branch,
            "commit": commit.get("hash", "")[:8],
            "commit_message": commit.get("message", ""),
            "author": commit.get("author", ""),
            "clean": status.get("clean", True),
            "modified_count": status.get("modified_count", 0),
            "untracked_count": status.get("untracked_count", 0),
            "recent_commits": [r["summary"] for r in recent],
        }
