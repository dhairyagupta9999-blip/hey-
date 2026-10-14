"""Project Model and Digital Environment Representation for Hey Jev.

Defines:
- Project dataclass: formal representation of user development projects and workspaces
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Project:
    """Formal model of an active or tracked user workspace/repository."""
    project_id: str = field(default_factory=lambda: f"proj_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    root_path: str = ""
    repository: str = ""
    repository_type: str = "git"
    languages: List[str] = field(default_factory=list)
    frameworks: List[str] = field(default_factory=list)
    status: str = "active"  # "active", "archived", "paused"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_activity_at: float = field(default_factory=time.time)
    active: bool = False
    owner: str = "user"
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "root_path": self.root_path,
            "repository": self.repository,
            "repository_type": self.repository_type,
            "languages": list(self.languages),
            "frameworks": list(self.frameworks),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_activity_at": self.last_activity_at,
            "active": self.active,
            "owner": self.owner,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Project:
        d = dict(data)
        return cls(**d)
