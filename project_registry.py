"""Project Registry for Persistent Workspace and Project Management in Hey Jev.

Manages:
- Persistent project records in SQLite
- Active project resolution
- Search across project names, paths, tags, and languages
- EventBus lifecycle notifications
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

import config
from event_bus import EventBus
from project_model import Project


class ProjectRegistry:
    """Thread-safe persistent registry of user development projects."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        if db_path is None:
            self.db_path = os.path.join(config.APPDATA_DIR, "projects.db")
        else:
            self.db_path = db_path

        self.event_bus = event_bus or EventBus()
        self._lock = threading.RLock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self.db_path == ":memory:":
            if self._mem_conn is None:
                self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._mem_conn.row_factory = sqlite3.Row
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    root_path TEXT NOT NULL,
                    repository TEXT,
                    repository_type TEXT,
                    languages TEXT,
                    frameworks TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_activity_at REAL NOT NULL,
                    active INTEGER NOT NULL,
                    owner TEXT,
                    tags TEXT,
                    metadata TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proj_active ON projects(active);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_proj_path ON projects(root_path);")

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        d = dict(row)
        d["languages"] = json.loads(d["languages"]) if d.get("languages") else []
        d["frameworks"] = json.loads(d["frameworks"]) if d.get("frameworks") else []
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        d["active"] = bool(d["active"])
        return Project.from_dict(d)

    def register(self, project: Project) -> None:
        """Register or replace a project record."""
        with self._lock, self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO projects (
                    project_id, name, description, root_path, repository,
                    repository_type, languages, frameworks, status, created_at,
                    updated_at, last_activity_at, active, owner, tags, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    project.project_id,
                    project.name,
                    project.description,
                    os.path.abspath(project.root_path) if project.root_path else "",
                    project.repository,
                    project.repository_type,
                    json.dumps(project.languages),
                    json.dumps(project.frameworks),
                    project.status,
                    project.created_at,
                    project.updated_at,
                    project.last_activity_at,
                    1 if project.active else 0,
                    project.owner,
                    json.dumps(project.tags),
                    json.dumps(project.metadata),
                ),
            )

        self.event_bus.emit("project.registered", project.project_id, data={"name": project.name, "root_path": project.root_path})

    def update(self, project: Project) -> bool:
        """Update an existing project record."""
        project.updated_at = time.time()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE projects SET
                    name = ?, description = ?, root_path = ?, repository = ?,
                    repository_type = ?, languages = ?, frameworks = ?, status = ?,
                    updated_at = ?, last_activity_at = ?, active = ?, owner = ?,
                    tags = ?, metadata = ?
                WHERE project_id = ?;
                """,
                (
                    project.name,
                    project.description,
                    os.path.abspath(project.root_path) if project.root_path else "",
                    project.repository,
                    project.repository_type,
                    json.dumps(project.languages),
                    json.dumps(project.frameworks),
                    project.status,
                    project.updated_at,
                    project.last_activity_at,
                    1 if project.active else 0,
                    project.owner,
                    json.dumps(project.tags),
                    json.dumps(project.metadata),
                    project.project_id,
                ),
            )
            found = cursor.rowcount > 0

        if found:
            self.event_bus.emit("project.updated", project.project_id, data={"status": project.status})
        return found

    def get(self, project_id: str) -> Optional[Project]:
        """Fetch project by ID."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM projects WHERE project_id = ?;", (project_id,))
            row = cursor.fetchone()
            return self._row_to_project(row) if row else None

    def get_by_path(self, path: str) -> Optional[Project]:
        """Fetch project matching absolute root path."""
        norm_path = os.path.abspath(path)
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM projects WHERE root_path = ?;", (norm_path,))
            row = cursor.fetchone()
            return self._row_to_project(row) if row else None

    def list(self, active_only: bool = False) -> List[Project]:
        """List tracked projects."""
        query = "SELECT * FROM projects WHERE active = 1 ORDER BY updated_at DESC;" if active_only else "SELECT * FROM projects ORDER BY updated_at DESC;"
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(query)
            return [self._row_to_project(row) for row in cursor.fetchall()]

    def search(self, query: str) -> List[Project]:
        """Search projects by name, path, tag, or language."""
        q_lower = query.lower()
        all_projects = self.list()
        matches = []
        for p in all_projects:
            text = f"{p.name} {p.description} {p.root_path} {' '.join(p.languages)} {' '.join(p.tags)}".lower()
            if q_lower in text:
                matches.append(p)
        return matches

    def set_active(self, project_id: str) -> bool:
        """Set a single active project, clearing active state on all others."""
        with self._lock, self._get_connection() as conn:
            conn.execute("UPDATE projects SET active = 0;")
            cursor = conn.execute("UPDATE projects SET active = 1, last_activity_at = ? WHERE project_id = ?;", (time.time(), project_id))
            activated = cursor.rowcount > 0

        if activated:
            self.event_bus.emit("project.activated", project_id, data={})
        return activated

    def get_active(self) -> Optional[Project]:
        """Get the currently active project."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM projects WHERE active = 1 LIMIT 1;")
            row = cursor.fetchone()
            return self._row_to_project(row) if row else None

    # Convenience aliases
    set_active_project = set_active
    get_active_project = get_active

    def delete(self, project_id: str) -> bool:
        """Remove a project from the registry."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE project_id = ?;", (project_id,))
            return cursor.rowcount > 0

    def __len__(self) -> int:
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM projects;")
            return int(cursor.fetchone()[0])
