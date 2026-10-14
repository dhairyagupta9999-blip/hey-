"""Skill Registry and Version-Pinned Storage for Hey Jev.

Manages reusable skill workflows, explicit semantic version pinning,
and capability validation.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from skill_model import SkillDefinition, SkillTrustLevel


class SkillRegistry:
    """Thread-safe dynamic registry of reusable skills."""

    def __init__(self) -> None:
        # Map: skill_id -> Dict[version, SkillDefinition]
        self._skills: Dict[str, Dict[str, SkillDefinition]] = {}
        # Map: skill_id -> latest_version
        self._latest_version: Dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, skill_def: SkillDefinition) -> None:
        """Register a skill definition with explicit semantic versioning."""
        with self._lock:
            sid = skill_def.skill_id
            ver = skill_def.version
            if sid not in self._skills:
                self._skills[sid] = {}
            self._skills[sid][ver] = skill_def
            self._latest_version[sid] = ver

    def remove(self, skill_id: str, version: Optional[str] = None) -> bool:
        """Remove a skill or a specific version of a skill."""
        with self._lock:
            if "@" in skill_id and version is None:
                parts = skill_id.split("@", 1)
                skill_id, version = parts[0], parts[1]

            if skill_id not in self._skills:
                return False

            if version is not None:
                found = version in self._skills[skill_id]
                self._skills[skill_id].pop(version, None)
                if not self._skills[skill_id]:
                    self._skills.pop(skill_id, None)
                    self._latest_version.pop(skill_id, None)
                elif self._latest_version.get(skill_id) == version:
                    # Pick remaining latest version
                    self._latest_version[skill_id] = list(self._skills[skill_id].keys())[-1]
                return found
            else:
                self._skills.pop(skill_id, None)
                self._latest_version.pop(skill_id, None)
                return True

    def get(self, skill_id: str, version: Optional[str] = None) -> Optional[SkillDefinition]:
        """Fetch a skill definition, supporting pinned versioning (e.g. 'downloads.organization@1.0')."""
        with self._lock:
            if "@" in skill_id and version is None:
                parts = skill_id.split("@", 1)
                skill_id, version = parts[0], parts[1]

            versions = self._skills.get(skill_id)
            if not versions:
                return None

            if version is not None:
                return versions.get(version)
            latest = self._latest_version.get(skill_id)
            return versions.get(latest) if latest else None

    def list(self, category: Optional[str] = None) -> List[SkillDefinition]:
        """List latest version of all registered skills, optionally filtered by category."""
        with self._lock:
            skills = []
            for sid, ver in self._latest_version.items():
                s = self._skills[sid].get(ver)
                if s:
                    if category is None or s.category.lower() == category.lower():
                        skills.append(s)
            return skills

    def list_all_versions(self, skill_id: str) -> List[SkillDefinition]:
        """List all versions registered for a given skill."""
        with self._lock:
            return list(self._skills.get(skill_id, {}).values())

    def search(self, query: str) -> List[SkillDefinition]:
        """Search skills by query across name, description, tags, and category."""
        q_lower = query.lower()
        words = set(re.findall(r"\w+", q_lower))

        with self._lock:
            scored: List[Tuple[float, SkillDefinition]] = []
            for s in self.list():
                score = 0.0
                if q_lower in s.skill_id.lower():
                    score += 5.0
                if q_lower in s.name.lower():
                    score += 4.0
                if any(q_lower in t.lower() for t in s.tags):
                    score += 3.0
                if q_lower in s.category.lower():
                    score += 2.0
                if q_lower in s.description.lower():
                    score += 1.0

                text = f"{s.name} {s.description} {' '.join(s.tags)} {s.category}".lower()
                overlap = sum(1 for w in words if w in text)
                score += overlap * 0.5

                if score > 0.0:
                    scored.append((score, s))

            scored.sort(key=lambda item: -item[0])
            return [item[1] for item in scored]

    def find_by_capability(self, capability: str) -> List[SkillDefinition]:
        """Find skills requiring a given capability."""
        cap_lower = capability.lower()
        with self._lock:
            return [
                s for s in self.list()
                if any(cap_lower in c.lower() for c in s.required_capabilities)
            ]

    def find_by_tool(self, tool_id: str) -> List[SkillDefinition]:
        """Find skills that compose a specific tool."""
        tid_lower = tool_id.lower()
        with self._lock:
            return [
                s for s in self.list()
                if any(tid_lower in t.lower() for t in s.required_tools)
            ]

    def validate(self, skill_def: SkillDefinition) -> Tuple[bool, List[str]]:
        """Validate structural integrity and security metadata of a skill."""
        errors: List[str] = []
        if not skill_def.skill_id or not re.match(r"^[a-zA-Z0-9_\.]+$", skill_def.skill_id):
            errors.append(f"Invalid skill_id '{skill_def.skill_id}'. Must be alphanumeric with dots/underscores.")
        if not skill_def.name:
            errors.append("Skill must provide a non-empty name.")
        if not skill_def.version:
            errors.append("Skill must declare a version.")
        if not skill_def.license:
            errors.append("Skill must declare a license (e.g. MIT, Apache-2.0).")
        return len(errors) == 0, errors

    def __len__(self) -> int:
        with self._lock:
            return len(self._skills)
