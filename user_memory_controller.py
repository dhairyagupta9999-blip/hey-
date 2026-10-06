"""User Memory Controller and Conversational Interface for Hey Jev.

Provides:
- remember(content, category, ...)
- forget(query_or_id)
- show_memory(...)
- explain(memory_id) -> Provenance and metadata without hidden CoT leakage
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from memory_model import (
    EpistemicStatus,
    MemoryRecord,
    MemorySensitivity,
    MemorySource,
    MemoryStatus,
    MemoryType,
)
from memory_store import MemoryStore


class UserMemoryController:
    """Conversational controller for user-directed memory operations."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def remember(
        self,
        content: str,
        category: MemoryType = MemoryType.USER_PREFERENCE,
        project_id: Optional[str] = None,
        importance: float = 0.8,
        tags: Optional[List[str]] = None,
        sensitivity: MemorySensitivity = MemorySensitivity.PRIVATE,
    ) -> MemoryRecord:
        """Store an explicit user instruction into persistent memory."""
        rec = MemoryRecord(
            type=category,
            content=content,
            summary=content[:100],
            source=MemorySource.USER,
            epistemic_status=EpistemicStatus.FACT,
            importance=importance,
            confidence=1.0,
            project_id=project_id,
            tags=list(tags or []),
            sensitivity=sensitivity,
            status=MemoryStatus.ACTIVE,
        )
        self.store.add(rec)
        return rec

    def forget(self, query_or_id: str) -> bool:
        """Forget a memory by exact ID or best query match."""
        # Check if direct ID
        direct = self.store.get(query_or_id)
        if direct:
            return self.store.delete(direct.memory_id)

        # Otherwise search by query and delete top match
        matches = self.store.search(query_or_id, limit=1)
        if matches:
            return self.store.delete(matches[0].memory_id)
        return False

    def show_memory(
        self,
        project_id: Optional[str] = None,
        category: Optional[MemoryType] = None,
        limit: int = 20,
    ) -> List[MemoryRecord]:
        """List active user memories filtered by project and category."""
        flt: Dict[str, Any] = {"status": MemoryStatus.ACTIVE}
        if project_id:
            flt["project_id"] = project_id
        if category:
            flt["type"] = category
        return self.store.list(filters=flt, limit=limit)

    def explain(self, memory_id: str) -> Dict[str, Any]:
        """Provide transparent attribution and provenance for a remembered item."""
        rec = self.store.get(memory_id)
        if not rec:
            return {"error": f"Memory '{memory_id}' not found."}

        return {
            "memory_id": rec.memory_id,
            "source": rec.source.value,
            "epistemic_status": rec.epistemic_status.value,
            "recorded_at": rec.created_at,
            "scope": rec.scope,
            "project_id": rec.project_id,
            "confidence": rec.confidence,
            "importance": rec.importance,
            "tags": rec.tags,
            "status": rec.status.value,
            "superseded_by": rec.superseded_by,
        }
