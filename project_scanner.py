"""Bounded Project Discovery and Signal Inspection for Hey Jev.

Scans permitted workspace directories for project markers:
- .git directory
- pyproject.toml, requirements.txt, setup.py (Python)
- package.json, tsconfig.json (JavaScript/TypeScript)
- Cargo.toml (Rust)
- *.sln, *.csproj (C# / .NET)
- go.mod (Go)

Safety:
- Bounded depth limits (default max_depth = 3)
- Explicit prohibition against root-level sweeps (e.g. C:\\, Program Files, Windows, AppData)
- Ignores node_modules, .venv, .git internals during tree traversal
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from project_model import Project
from project_registry import ProjectRegistry


class ProjectScanner:
    """Discovers development projects in bounded directory trees."""

    FORBIDDEN_ROOTS = {
        os.path.abspath(os.environ.get("SystemDrive", "C:") + "\\"),
        os.path.abspath(os.environ.get("SystemRoot", "C:\\Windows")),
        os.path.abspath(os.environ.get("ProgramFiles", "C:\\Program Files")),
        os.path.abspath(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")),
        os.path.abspath(os.environ.get("APPDATA", "")),
        os.path.abspath(os.environ.get("LOCALAPPDATA", "")),
    }

    IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        ".idea",
        ".vscode",
    }

    PROJECT_SIGNALS = {
        ".git": ("git", None),
        "pyproject.toml": ("Python", None),
        "requirements.txt": ("Python", None),
        "setup.py": ("Python", None),
        "package.json": ("JavaScript", None),
        "tsconfig.json": ("TypeScript", None),
        "Cargo.toml": ("Rust", None),
        "go.mod": ("Go", None),
        "pom.xml": ("Java", "Maven"),
        "build.gradle": ("Java", "Gradle"),
    }

    def __init__(self, registry: Optional[ProjectRegistry] = None) -> None:
        self.registry = registry

    def is_safe_root(self, root_dir: str) -> bool:
        """Verify the directory is safe to scan and not a protected system root."""
        norm = os.path.abspath(root_dir)
        if norm in self.FORBIDDEN_ROOTS:
            return False
        # Do not scan top-level root
        drive, tail = os.path.splitdrive(norm)
        if tail in ("\\", "/", ""):
            return False
        return os.path.exists(norm) and os.path.isdir(norm)

    def scan_directory(self, root_dir: str, max_depth: int = 3) -> List[Project]:
        """Scan directory tree up to max_depth and return discovered projects."""
        if not self.is_safe_root(root_dir):
            raise ValueError(f"Scan path '{root_dir}' is rejected by safety policy.")

        base_norm = os.path.abspath(root_dir)
        discovered: List[Project] = []

        for current_root, dirs, files in os.walk(base_norm):
            rel = os.path.relpath(current_root, base_norm)
            depth = 0 if rel == "." else len(rel.split(os.sep))
            if depth > max_depth:
                dirs.clear()
                continue

            # Remove ignored directories in-place to prevent traversal
            dirs[:] = [d for d in dirs if d.lower() not in self.IGNORE_DIRS]

            # Inspect directory contents for project markers
            markers_found: Set[str] = set()
            languages: Set[str] = set()
            frameworks: Set[str] = set()

            for item in dirs + files:
                if item in self.PROJECT_SIGNALS:
                    lang, fw = self.PROJECT_SIGNALS[item]
                    markers_found.add(item)
                    if lang:
                        languages.add(lang)
                    if fw:
                        frameworks.add(fw)

            # Check for .sln
            for f in files:
                if f.endswith(".sln"):
                    markers_found.add(".sln")
                    languages.add("C#")
                    break

            if markers_found:
                # Detect framework heuristics
                if "package.json" in markers_found:
                    pkg_path = os.path.join(current_root, "package.json")
                    try:
                        with open(pkg_path, "r", encoding="utf-8", errors="ignore") as pf:
                            txt = pf.read().lower()
                            if "react" in txt:
                                frameworks.add("React")
                            if "next" in txt:
                                frameworks.add("Next.js")
                            if "vue" in txt:
                                frameworks.add("Vue")
                    except Exception:
                        pass

                folder_name = os.path.basename(current_root)
                proj = Project(
                    name=folder_name,
                    root_path=os.path.abspath(current_root),
                    languages=sorted(languages),
                    frameworks=sorted(frameworks),
                    repository_type="git" if ".git" in markers_found else "local",
                    tags=sorted(markers_found),
                )
                discovered.append(proj)
                if self.registry:
                    self.registry.register(proj)

        return discovered
