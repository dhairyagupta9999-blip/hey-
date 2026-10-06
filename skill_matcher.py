"""Skill Matcher and Natural-Language Goal Routing for Hey Jev.

Inspects natural-language goals, task requirements, capabilities, and permissions
to return ranked, scored candidate skills.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from agent_task import TaskPermission
from skill_model import SkillDefinition
from skill_registry import SkillRegistry


@dataclass
class ScoredSkill:
    """Skill candidate with matching confidence and rationale."""
    skill: SkillDefinition
    score: float
    matched_capabilities: List[str]
    matched_tags: List[str]
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill.skill_id,
            "version": self.skill.version,
            "name": self.skill.name,
            "score": round(self.score, 3),
            "matched_capabilities": self.matched_capabilities,
            "rationale": self.rationale,
        }


class SkillMatcher:
    """Discovers and ranks candidate skills for a natural-language goal."""

    # High-priority domain pattern rules
    DOMAIN_PATTERNS = [
        (
            re.compile(r"\b(organize|sort|clean up|archive)\b.*\b(download|downloads|files|folder)\b", re.IGNORECASE),
            "downloads.organization",
            1.0,
        ),
        (
            re.compile(r"\b(fourth|4th|picture|image|photo|wallpaper|desktop background)\b", re.IGNORECASE),
            "windows.file_management",
            0.95,
        ),
        (
            re.compile(r"\b(wallpaper|background image)\b", re.IGNORECASE),
            "windows.wallpaper",
            0.95,
        ),
        (
            re.compile(r"\b(research|compare|best|investigate|hotels|laptops?)\b", re.IGNORECASE),
            "web.research",
            0.95,
        ),
        (
            re.compile(r"\b(browse|navigate|open website|open tab|fill form)\b", re.IGNORECASE),
            "web.navigation",
            0.90,
        ),
        (
            re.compile(r"\b(app|application|launch|close app|focus)\b", re.IGNORECASE),
            "windows.app_control",
            0.90,
        ),
        (
            re.compile(r"\b(diagnostic|health|check project|telemetry)\b", re.IGNORECASE),
            "task.diagnostics",
            0.85,
        ),
    ]

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def match(
        self,
        goal: str,
        caller_permissions: Optional[List[TaskPermission]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[ScoredSkill]:
        """Rank skills matching a natural-language goal."""
        goal_lower = goal.lower()
        words = set(re.findall(r"\w+", goal_lower))
        granted_perms = set(caller_permissions or [])

        # 1. Check direct pattern matches
        direct_matches: Dict[str, float] = {}
        for pattern, skill_id, base_score in self.DOMAIN_PATTERNS:
            if pattern.search(goal):
                direct_matches[skill_id] = base_score

        all_skills = self.registry.list()
        scored: List[ScoredSkill] = []

        for skill in all_skills:
            score = direct_matches.get(skill.skill_id, 0.0)
            matched_caps = []
            matched_tags = []

            # Check capability overlap
            for cap in skill.required_capabilities:
                cap_words = set(re.findall(r"\w+", cap.lower()))
                if cap_words & words:
                    matched_caps.append(cap)
                    score += 0.20

            # Check tag overlap
            for tag in skill.tags:
                if tag.lower() in goal_lower:
                    matched_tags.append(tag)
                    score += 0.15

            # Text overlap with skill name and description
            text = f"{skill.name} {skill.description}".lower()
            text_words = set(re.findall(r"\w+", text))
            overlap = len(words & text_words)
            score += min(0.30, overlap * 0.05)

            # Permission check
            if skill.permissions and not all(p in granted_perms for p in skill.permissions):
                score *= 0.5

            if score > 0.0:
                score = min(1.0, score)
                rationale = (
                    f"Score={score:.2f} "
                    f"(Matched {len(matched_caps)} caps, {len(matched_tags)} tags)"
                )
                scored.append(
                    ScoredSkill(
                        skill=skill,
                        score=score,
                        matched_capabilities=matched_caps,
                        matched_tags=matched_tags,
                        rationale=rationale,
                    )
                )

        scored.sort(key=lambda item: -item.score)
        return scored
