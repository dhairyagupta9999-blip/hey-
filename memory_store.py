"""Persistent Memory Store and SQLite WAL Implementation for Hey Jev.

Provides:
- MemoryStore abstract base interface
- SqliteMemoryStore with WAL journaling, indexing, and thread-safety
- Keyword search, structured multi-field filtering, and temporal queries
- Atomic memory supersession and consolidation
- Least-privilege safe pruning (preserves user preferences & project decisions)
- EventBus audit emissions
"""
from __future__ import annotations

import abc
import json
import os
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import config
from event_bus import EventBus
from memory_model import (
    EpistemicStatus,
    MemoryRecord,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
    MemoryType,
)


class MemoryStore(abc.ABC):
    """Abstract interface defining required memory repository capabilities."""

    @abc.abstractmethod
    def add(self, record: MemoryRecord) -> str:
        """Persist a new memory record and return its ID."""
        pass

    @abc.abstractmethod
    def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch a memory record by ID."""
        pass

    @abc.abstractmethod
    def update(self, record: MemoryRecord) -> bool:
        """Update an existing memory record in place."""
        pass

    @abc.abstractmethod
    def supersede(self, old_id: str, new_record: MemoryRecord) -> str:
        """Atomically mark old record as SUPERSEDED and insert new active record."""
        pass

    @abc.abstractmethod
    def delete(self, memory_id: str, hard: bool = False) -> bool:
        """Delete or soft-delete a memory record."""
        pass

    @abc.abstractmethod
    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
    ) -> List[MemoryRecord]:
        """Perform keyword search with structured criteria."""
        pass

    @abc.abstractmethod
    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        """Enumerate memory records matching structured filters."""
        pass

    @abc.abstractmethod
    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count records matching criteria."""
        pass

    @abc.abstractmethod
    def prune(self, criteria: Optional[Dict[str, Any]] = None) -> int:
        """Safely prune expired or obsolete memories."""
        pass

    @abc.abstractmethod
    def export(self, file_path: Optional[str] = None) -> str:
        """Export all memories to JSON formatted string or file."""
        pass

    @abc.abstractmethod
    def consolidate(
        self,
        records: List[MemoryRecord],
        consolidated_content: str,
        summary: str,
        record_type: MemoryType = MemoryType.PROJECT,
    ) -> MemoryRecord:
        """Consolidate multiple related records into a single coherent memory."""
        pass


class SqliteMemoryStore(MemoryStore):
    """Production SQLite-backed memory store with WAL mode and atomic transactions."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        if db_path is None:
            self.db_path = os.path.join(config.APPDATA_DIR, "memory.db")
        else:
            self.db_path = db_path

        self.event_bus = event_bus or EventBus()
        self._lock = threading.RLock()
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._cache: Dict[str, Tuple[float, List[MemoryRecord]]] = {}
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
        """Initialize tables and indexes with WAL mode."""
        with self._lock, self._get_connection() as conn:
            if self.db_path != ":memory:":
                conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    source TEXT NOT NULL,
                    epistemic_status TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    entity_ids TEXT,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_accessed_at REAL NOT NULL,
                    expires_at REAL,
                    valid_from REAL,
                    valid_until REAL,
                    superseded_at REAL,
                    superseded_by TEXT,
                    tags TEXT,
                    task_id TEXT,
                    mission_id TEXT,
                    project_id TEXT,
                    agent_id TEXT,
                    sensitivity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checksum TEXT,
                    metadata TEXT
                );
            """)

            # Indexes for fast retrieval
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_type ON memories(type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_project ON memories(project_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_status ON memories(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_importance ON memories(importance);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_created ON memories(created_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_sens ON memories(sensitivity);")

    def _invalidate_cache(self) -> None:
        self._cache.clear()

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        d = dict(row)
        d["entity_ids"] = json.loads(d["entity_ids"]) if d.get("entity_ids") else []
        d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        d["metadata"] = json.loads(d["metadata"]) if d.get("metadata") else {}
        return MemoryRecord.from_dict(d)

    def _insert_record(self, conn: sqlite3.Connection, record: MemoryRecord) -> None:
        conn.execute(
            """
            INSERT OR REPLACE INTO memories (
                memory_id, type, content, summary, source, epistemic_status,
                scope, entity_ids, importance, confidence, created_at,
                updated_at, last_accessed_at, expires_at, valid_from,
                valid_until, superseded_at, superseded_by, tags, task_id,
                mission_id, project_id, agent_id, sensitivity, status,
                checksum, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                record.memory_id,
                record.type.value,
                record.content,
                record.summary,
                record.source.value,
                record.epistemic_status.value,
                record.scope,
                json.dumps(record.entity_ids),
                record.importance,
                record.confidence,
                record.created_at,
                record.updated_at,
                record.last_accessed_at,
                record.expires_at,
                record.valid_from,
                record.valid_until,
                record.superseded_at,
                record.superseded_by,
                json.dumps(record.tags),
                record.task_id,
                record.mission_id,
                record.project_id,
                record.agent_id,
                record.sensitivity.value,
                record.status.value,
                record.checksum,
                json.dumps(record.metadata),
            ),
        )

    def add(self, record: MemoryRecord) -> str:
        """Persist a new memory record."""
        with self._lock, self._get_connection() as conn:
            self._insert_record(conn, record)
            self._invalidate_cache()

        self.event_bus.emit(
            "memory.created",
            record.memory_id,
            data={"type": record.type.value, "project_id": record.project_id, "importance": record.importance},
        )
        return record.memory_id

    def get(self, memory_id: str, touch: bool = False) -> Optional[MemoryRecord]:
        """Fetch memory record by ID, optionally updating last_accessed_at."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM memories WHERE memory_id = ?;", (memory_id,))
            row = cursor.fetchone()
            if not row:
                return None
            if touch:
                now = time.time()
                conn.execute("UPDATE memories SET last_accessed_at = ? WHERE memory_id = ?;", (now, memory_id))
            rec = self._row_to_record(row)
            return rec

    def update(self, record: MemoryRecord) -> bool:
        """Update an existing memory record."""
        record.updated_at = time.time()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                UPDATE memories SET
                    type = ?, content = ?, summary = ?, source = ?, epistemic_status = ?,
                    scope = ?, entity_ids = ?, importance = ?, confidence = ?,
                    updated_at = ?, expires_at = ?, valid_from = ?, valid_until = ?,
                    superseded_at = ?, superseded_by = ?, tags = ?, task_id = ?,
                    mission_id = ?, project_id = ?, agent_id = ?, sensitivity = ?,
                    status = ?, checksum = ?, metadata = ?
                WHERE memory_id = ?;
                """,
                (
                    record.type.value,
                    record.content,
                    record.summary,
                    record.source.value,
                    record.epistemic_status.value,
                    record.scope,
                    json.dumps(record.entity_ids),
                    record.importance,
                    record.confidence,
                    record.updated_at,
                    record.expires_at,
                    record.valid_from,
                    record.valid_until,
                    record.superseded_at,
                    record.superseded_by,
                    json.dumps(record.tags),
                    record.task_id,
                    record.mission_id,
                    record.project_id,
                    record.agent_id,
                    record.sensitivity.value,
                    record.status.value,
                    record.checksum,
                    json.dumps(record.metadata),
                    record.memory_id,
                ),
            )
            found = cursor.rowcount > 0
            if found:
                self._invalidate_cache()

        if found:
            self.event_bus.emit("memory.updated", record.memory_id, data={"status": record.status.value})
        return found

    def supersede(self, old_id: str, new_record: MemoryRecord) -> str:
        """Atomically mark old record as SUPERSEDED and insert new active record."""
        now = time.time()
        with self._lock, self._get_connection() as conn:
            # 1. Update old record
            conn.execute(
                """
                UPDATE memories SET
                    status = ?,
                    superseded_at = ?,
                    superseded_by = ?,
                    valid_until = ?,
                    updated_at = ?
                WHERE memory_id = ?;
                """,
                (
                    MemoryStatus.SUPERSEDED.value,
                    now,
                    new_record.memory_id,
                    now,
                    now,
                    old_id,
                ),
            )
            # 2. Insert new record
            new_record.valid_from = now
            new_record.created_at = now
            new_record.updated_at = now
            new_record.status = MemoryStatus.ACTIVE
            self._insert_record(conn, new_record)
            self._invalidate_cache()

        self.event_bus.emit(
            "memory.superseded",
            old_id,
            data={"superseded_by": new_record.memory_id},
        )
        return new_record.memory_id

    def delete(self, memory_id: str, hard: bool = False) -> bool:
        """Delete or soft-delete a memory record."""
        with self._lock, self._get_connection() as conn:
            if hard:
                cursor = conn.execute("DELETE FROM memories WHERE memory_id = ?;", (memory_id,))
            else:
                cursor = conn.execute(
                    "UPDATE memories SET status = ?, updated_at = ? WHERE memory_id = ?;",
                    (MemoryStatus.DELETED.value, time.time(), memory_id),
                )
            deleted = cursor.rowcount > 0
            if deleted:
                self._invalidate_cache()

        if deleted:
            self.event_bus.emit("memory.deleted", memory_id, data={"hard": hard})
        return deleted

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 20,
    ) -> List[MemoryRecord]:
        """Perform keyword search with structured filtering and keyword match scoring."""
        # 1. Retrieve candidates matching structured filters
        candidates = self.list(filters=filters, limit=max(limit * 3, 50))
        if not query.strip():
            return candidates[:limit]

        # 2. Rank candidates by keyword overlap
        q_tokens = set(re.findall(r"\w+", query.lower()))
        scored: List[Tuple[float, MemoryRecord]] = []

        for rec in candidates:
            text = f"{rec.content} {rec.summary} {' '.join(rec.tags)} {' '.join(rec.entity_ids)}".lower()
            rec_tokens = set(re.findall(r"\w+", text))
            if not rec_tokens:
                continue

            overlap = len(q_tokens & rec_tokens)
            if overlap == 0:
                # Substring check fallback
                if query.lower() in text:
                    overlap = 1
                else:
                    continue

            # Normalized overlap score + importance boost
            score = (overlap / max(1, len(q_tokens))) * 0.7 + (rec.importance * 0.3)
            scored.append((score, rec))

        scored.sort(key=lambda item: -item[0])
        results = [item[1] for item in scored[:limit]]

        self.event_bus.emit("memory.retrieved", query, data={"count": len(results)})
        return results

    def list(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[MemoryRecord]:
        """Enumerate memories matching structured filter criteria."""
        flt = filters or {}
        clauses: List[str] = []
        params: List[Any] = []

        # Filter by status (default ACTIVE unless explicitly requested)
        if "status" in flt:
            st = flt["status"]
            val = st.value if isinstance(st, MemoryStatus) else str(st)
            clauses.append("status = ?")
            params.append(val)
        elif not flt.get("include_all_statuses", False):
            clauses.append("status = ?")
            params.append(MemoryStatus.ACTIVE.value)

        # Filter by type
        if "type" in flt:
            tp = flt["type"]
            val = tp.value if isinstance(tp, MemoryType) else str(tp)
            clauses.append("type = ?")
            params.append(val)

        # Filter by project_id
        if "project_id" in flt:
            clauses.append("project_id = ?")
            params.append(flt["project_id"])

        # Filter by scope
        if "scope" in flt:
            clauses.append("scope = ?")
            params.append(flt["scope"])

        # Filter by source
        if "source" in flt:
            src = flt["source"]
            val = src.value if isinstance(src, MemorySource) else str(src)
            clauses.append("source = ?")
            params.append(val)

        # Filter by sensitivity
        if "sensitivity" in flt:
            sens = flt["sensitivity"]
            val = sens.value if isinstance(sens, MemorySensitivity) else str(sens)
            clauses.append("sensitivity = ?")
            params.append(val)

        # Filter by max_sensitivity
        if "max_sensitivity" in flt:
            allowed = self._allowed_sensitivities(flt["max_sensitivity"])
            placeholders = ", ".join(["?"] * len(allowed))
            clauses.append(f"sensitivity IN ({placeholders})")
            params.extend(allowed)

        # Filter by agent_id
        if "agent_id" in flt:
            clauses.append("agent_id = ?")
            params.append(flt["agent_id"])

        # Filter by mission_id
        if "mission_id" in flt:
            clauses.append("mission_id = ?")
            params.append(flt["mission_id"])

        # Filter by date ranges
        if "since" in flt:
            clauses.append("created_at >= ?")
            params.append(float(flt["since"]))
        if "until" in flt:
            clauses.append("created_at <= ?")
            params.append(float(flt["until"]))

        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = f"SELECT * FROM memories{where_clause} ORDER BY importance DESC, created_at DESC LIMIT ?;"
        params.append(limit)

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            return [self._row_to_record(row) for row in cursor.fetchall()]

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Count memory rows matching filters."""
        flt = filters or {}
        clauses: List[str] = []
        params: List[Any] = []

        if "status" in flt:
            st = flt["status"]
            clauses.append("status = ?")
            params.append(st.value if isinstance(st, MemoryStatus) else str(st))
        elif not flt.get("include_all_statuses", False):
            clauses.append("status = ?")
            params.append(MemoryStatus.ACTIVE.value)

        if "project_id" in flt:
            clauses.append("project_id = ?")
            params.append(flt["project_id"])

        if "type" in flt:
            tp = flt["type"]
            clauses.append("type = ?")
            params.append(tp.value if isinstance(tp, MemoryType) else str(tp))

        where_clause = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(f"SELECT COUNT(*) FROM memories{where_clause};", tuple(params))
            return int(cursor.fetchone()[0])

    def prune(self, criteria: Optional[Dict[str, Any]] = None) -> int:
        """Safely prune expired, obsolete memories.

        Invariants:
        - NEVER deletes USER_PREFERENCE records.
        - NEVER deletes PROJECT_DECISION records.
        """
        crit = criteria or {}
        now = time.time()
        max_age = crit.get("max_age_seconds")
        min_importance = crit.get("min_importance", 0.2)

        protected_types = [
            MemoryType.USER_PREFERENCE.value,
            MemoryType.PROJECT_DECISION.value,
        ]

        with self._lock, self._get_connection() as conn:
            # 1. Prune explicitly expired memories
            q1 = "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ? AND type NOT IN (?, ?);"
            c1 = conn.execute(q1, (now, protected_types[0], protected_types[1]))
            pruned_count = c1.rowcount

            # 2. Prune soft-deleted memories
            q2 = "DELETE FROM memories WHERE status = ?;"
            c2 = conn.execute(q2, (MemoryStatus.DELETED.value,))
            pruned_count += c2.rowcount

            # 3. Prune low-importance stale memories if max_age specified
            if max_age:
                cutoff = now - max_age
                q3 = """
                    DELETE FROM memories
                    WHERE created_at < ? AND importance < ?
                    AND type NOT IN (?, ?)
                    AND status = ?;
                """
                c3 = conn.execute(
                    q3,
                    (cutoff, min_importance, protected_types[0], protected_types[1], MemoryStatus.SUPERSEDED.value),
                )
                pruned_count += c3.rowcount

            if pruned_count > 0:
                self._invalidate_cache()

        return pruned_count

    def export(self, file_path: Optional[str] = None) -> str:
        """Export all active and historical memories to JSON."""
        records = self.list(filters={"include_all_statuses": True}, limit=10000)
        data = [r.to_dict() for r in records]
        content = json.dumps({"count": len(data), "memories": data}, indent=2)
        if file_path:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        return content

    def consolidate(
        self,
        records: List[MemoryRecord],
        consolidated_content: str,
        summary: str,
        record_type: MemoryType = MemoryType.PROJECT,
    ) -> MemoryRecord:
        """Consolidate multiple related records into a single coherent memory."""
        if not records:
            raise ValueError("Consolidation requires at least one input memory record.")

        # Aggregate entity ids and tags
        entities = sorted(set(e for r in records for e in r.entity_ids))
        tags = sorted(set(t for r in records for t in r.tags))
        project_ids = set(r.project_id for r in records if r.project_id)
        proj_id = list(project_ids)[0] if project_ids else None

        avg_importance = max(r.importance for r in records)
        avg_confidence = sum(r.confidence for r in records) / len(records)

        now = time.time()
        new_rec = MemoryRecord(
            type=record_type,
            content=consolidated_content,
            summary=summary,
            source=MemorySource.SYSTEM,
            epistemic_status=EpistemicStatus.FACT,
            entity_ids=entities,
            tags=tags,
            project_id=proj_id,
            importance=avg_importance,
            confidence=avg_confidence,
            created_at=now,
            updated_at=now,
            valid_from=now,
            status=MemoryStatus.ACTIVE,
            metadata={"consolidated_from": [r.memory_id for r in records]},
        )

        with self._lock, self._get_connection() as conn:
            for r in records:
                conn.execute(
                    """
                    UPDATE memories SET
                        status = ?,
                        superseded_at = ?,
                        superseded_by = ?,
                        valid_until = ?,
                        updated_at = ?
                    WHERE memory_id = ?;
                    """,
                    (
                        MemoryStatus.SUPERSEDED.value,
                        now,
                        new_rec.memory_id,
                        now,
                        now,
                        r.memory_id,
                    ),
                )
            self._insert_record(conn, new_rec)
            self._invalidate_cache()

        for r in records:
            self.event_bus.emit(
                "memory.superseded",
                r.memory_id,
                data={"superseded_by": new_rec.memory_id, "reason": "consolidation"},
            )
        self.event_bus.emit(
            "memory.consolidated",
            new_rec.memory_id,
            data={"sources": [r.memory_id for r in records]},
        )

        return new_rec

    def _allowed_sensitivities(self, max_sens: Union[str, MemorySensitivity]) -> List[str]:
        rank = {
            MemorySensitivity.PUBLIC: 1,
            MemorySensitivity.NORMAL: 2,
            MemorySensitivity.PRIVATE: 3,
            MemorySensitivity.SENSITIVE: 4,
        }
        val = max_sens if isinstance(max_sens, MemorySensitivity) else MemorySensitivity(str(max_sens).upper())
        limit_val = rank.get(val, 2)
        return [s.value for s, r in rank.items() if r <= limit_val]
