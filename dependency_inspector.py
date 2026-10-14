"""Dependency Inspector and Reviewable Installation Planner (Phase 24).

Separates dependency inspection from dependency installation:
- Evaluates package provenance, licensing, security risks, and stdlib alternatives.
- Enforces strict zero-silent-installation policy: global environments and system settings
  are NEVER modified silently.
- Dependency installation strictly requires:
  1. Adequate isolated virtual environment boundary.
  2. Explicit, human-authorized approval.
- Where adequate isolation is unavailable, stops at static inspection and flags
  BLOCKED_ISOLATION.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from agent_task import TaskPermission


class DependencyRiskLevel(str, Enum):
    NONE = "NONE"          # Standard library / already installed
    LOW = "LOW"            # Known trusted, permissive license, no post-install scripts
    MEDIUM = "MEDIUM"      # External library requiring isolated install
    HIGH = "HIGH"          # Native binaries, unverified publisher, or untrusted source


@dataclass
class InstallationPlan:
    """A formal, reviewable plan to install an external dependency."""
    package_name: str
    version_spec: str = ">=0.0"
    publisher: str = "PyPI"
    license: str = "Open Source"
    can_use_existing: bool = False
    existing_alternative: str = ""
    requires_network: bool = True
    requires_isolated_env: bool = True
    risk_level: DependencyRiskLevel = DependencyRiskLevel.LOW
    risk_details: str = ""
    approval_granted: bool = False
    install_command: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_name": self.package_name,
            "version_spec": self.version_spec,
            "publisher": self.publisher,
            "license": self.license,
            "can_use_existing": self.can_use_existing,
            "existing_alternative": self.existing_alternative,
            "requires_network": self.requires_network,
            "requires_isolated_env": self.requires_isolated_env,
            "risk_level": self.risk_level.value,
            "risk_details": self.risk_details,
            "approval_granted": self.approval_granted,
            "install_command": list(self.install_command),
        }


class DependencyInspector:
    """Inspects candidate dependencies, recommends stdlib alternatives, and gates installation."""

    # Python standard library modules frequently needed by extensions
    STDLIB_MODULES = {
        "sqlite3", "json", "csv", "zipfile", "tarfile", "wave", "hashlib",
        "urllib", "http", "os", "sys", "re", "shutil", "tempfile", "time",
        "datetime", "math", "random", "uuid", "pathlib", "ast", "inspect",
        "xml", "html", "gzip", "bz2", "lzma", "base64", "binascii", "ctypes",
    }

    def inspect_dependencies(
        self,
        declared_dependencies: List[str],
        declared_permissions: Optional[List[TaskPermission]] = None,
    ) -> Tuple[List[InstallationPlan], List[str]]:
        """Inspect list of declared dependencies and synthesize reviewable plans.
        
        Returns:
            (plans, warnings)
        """
        plans: List[InstallationPlan] = []
        warnings: List[str] = []
        perms = set(declared_permissions or [])

        for dep in declared_dependencies:
            clean_dep = dep.strip()
            if not clean_dep:
                continue

            # 1. Check if module is in Python stdlib
            if clean_dep.lower() in self.STDLIB_MODULES:
                plans.append(
                    InstallationPlan(
                        package_name=clean_dep,
                        version_spec="stdlib",
                        publisher="Python Software Foundation",
                        license="Python Software Foundation License",
                        can_use_existing=True,
                        existing_alternative=f"Standard library module '{clean_dep}'",
                        requires_network=False,
                        requires_isolated_env=False,
                        risk_level=DependencyRiskLevel.NONE,
                        risk_details="Built into Python; zero installation or network required.",
                        install_command=[],
                    )
                )
                continue

            # 2. Check if already installed in current active environment
            spec = importlib.util.find_spec(clean_dep)
            if spec is not None and spec.origin:
                plans.append(
                    InstallationPlan(
                        package_name=clean_dep,
                        version_spec="installed",
                        publisher="Local Environment",
                        license="Existing",
                        can_use_existing=True,
                        existing_alternative=f"Already available in current environment ({spec.origin})",
                        requires_network=False,
                        requires_isolated_env=False,
                        risk_level=DependencyRiskLevel.NONE,
                        risk_details="Package already installed in environment; no download needed.",
                        install_command=[],
                    )
                )
                continue

            # 3. External package requiring installation
            needs_network = True
            if TaskPermission.NETWORK_ACCESS not in perms:
                warnings.append(
                    f"Dependency '{clean_dep}' requires network access to download, but NETWORK_ACCESS is not declared."
                )

            plan = InstallationPlan(
                package_name=clean_dep,
                version_spec="latest_compatible",
                publisher="PyPI",
                license="External Package",
                can_use_existing=False,
                requires_network=needs_network,
                requires_isolated_env=True,
                risk_level=DependencyRiskLevel.MEDIUM,
                risk_details=(
                    f"External package '{clean_dep}' must be downloaded. "
                    "Requires dedicated isolated environment and explicit human approval."
                ),
                install_command=[sys.executable, "-m", "pip", "install", "--no-deps", clean_dep],
            )
            plans.append(plan)

        return plans, warnings

    def can_install_safely(
        self,
        plan: InstallationPlan,
        has_isolated_env: bool = False,
        human_approved: bool = False,
    ) -> Tuple[bool, str]:
        """Check whether dependency may be installed under current security controls."""
        if plan.can_use_existing:
            return True, f"'{plan.package_name}' already exists; installation bypassed."

        if not human_approved:
            return False, f"Installation of external dependency '{plan.package_name}' requires explicit human approval."

        if not has_isolated_env and plan.requires_isolated_env:
            return False, (
                f"Cannot install '{plan.package_name}': adequate isolated virtual environment is unavailable. "
                "Global or unisolated installation is strictly forbidden."
            )

        return True, f"Installation authorized for isolated environment."
