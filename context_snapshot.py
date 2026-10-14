"""Environment Context Snapshot for Hey Jev.

Captures active desktop, browser, and operational context without raw screenshots.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ContextSnapshot:
    """Live snapshot of user's active environment."""
    screen_summary: str = ""
    active_app: str = ""
    window_title: str = ""
    browser_url: str = ""
    active_project_id: Optional[str] = None
    active_mission_id: Optional[str] = None
    active_task_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ContextSnapshot:
        return cls(**data)
