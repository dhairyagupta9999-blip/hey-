"""Structured Incident Memory and Recurrence Analytics for Hey Jev (Phase 26).

Provides:
- IncidentRecord: Structured operational incident schema capturing symptoms, root cause,
  recovery actions, verified outcomes, and timing.
- IncidentMemoryStore: Bridges operational incident recording with SqliteMemoryStore.
- Recurrence Analytics: Detects flapping, recurring failure patterns, and regression frequency.
- Security: Guarantees zero secret exposure via sanitize_content on all persisted records.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from event_bus import EventBus
from memory_model import MemoryRecord, MemorySource, MemoryType, sanitize_content
from memory_store import MemoryStore
from operational_health import HealthState, SubsystemCategory

logger = logging.getLogger(__name__)


@dataclass
class IncidentRecord:
    """A durable, auditable record of an operational failure or degradation event."""
    incident_id: str
    component_id: str
    category: SubsystemCategory
    symptoms: List[str] = field(default_factory=list)
    verified_causes: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    recovery_attempts: List[Dict[str, Any]] = field(default_factory=list)
    final_state: HealthState = HealthState.NEEDS_ATTENTION
    verified_outcome: str = ""
    resolved: bool = False
    created_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    occurrence_count: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "incident_id": self.incident_id,
            "component_id": self.component_id,
            "category": self.category.value,
            "symptoms": list(self.symptoms),
            "verified_causes": list(self.verified_causes),
            "hypotheses": list(self.hypotheses),
            "recovery_attempts": list(self.recovery_attempts),
            "final_state": self.final_state.value,
            "verified_outcome": self.verified_outcome,
            "resolved": self.resolved,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "occurrence_count": self.occurrence_count,
        }


class IncidentMemoryStore:
    """Manages operational incident persistence in SqliteMemoryStore with recurrence analytics."""

    def __init__(
        self,
        memory_store: Optional[MemoryStore] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.memory_store = memory_store
        self.event_bus = event_bus or EventBus()
        self._lock = threading.RLock()
        self._in_memory_incidents: Dict[str, IncidentRecord] = {}

        if self.memory_store is not None:
            self._load_from_memory_store()

    def _load_from_memory_store(self) -> None:
        """Hydrate in-memory incident records from persistent SqliteMemoryStore across restarts."""
        try:
            records = self.memory_store.list(filters={"type": MemoryType.ENVIRONMENT_FACT}, limit=500)
            for rec in records:
                meta = rec.metadata or {}
                if "incident_id" in meta and "component_id" in meta:
                    inc_id = meta["incident_id"]
                    if inc_id not in self._in_memory_incidents:
                        try:
                            cat = SubsystemCategory(meta.get("category", SubsystemCategory.RUNTIME_COMPONENT.value))
                        except Exception:
                            cat = SubsystemCategory.RUNTIME_COMPONENT
                        try:
                            st = HealthState(meta.get("final_state", HealthState.NEEDS_ATTENTION.value))
                        except Exception:
                            st = HealthState.NEEDS_ATTENTION

                        self._in_memory_incidents[inc_id] = IncidentRecord(
                            incident_id=inc_id,
                            component_id=meta["component_id"],
                            category=cat,
                            symptoms=meta.get("symptoms", []),
                            verified_causes=meta.get("verified_causes", []),
                            hypotheses=meta.get("hypotheses", []),
                            recovery_attempts=meta.get("recovery_attempts", []),
                            final_state=st,
                            verified_outcome=meta.get("verified_outcome", ""),
                            resolved=meta.get("resolved", False),
                            created_at=meta.get("created_at", rec.created_at),
                            resolved_at=meta.get("resolved_at"),
                            occurrence_count=meta.get("occurrence_count", 1),
                        )
        except Exception as ex:
            logger.warning(f"Failed to load incidents from persistent memory store: {ex}")

    def _sanitize(self, val: Any) -> Any:
        if isinstance(val, str):
            return sanitize_content(val)
        if isinstance(val, list):
            return [self._sanitize(x) for x in val]
        if isinstance(val, dict):
            return {k: self._sanitize(v) for k, v in val.items()}
        return val

    def record_incident(
        self,
        component_id: str,
        category: SubsystemCategory,
        symptoms: List[str],
        verified_causes: Optional[List[str]] = None,
        hypotheses: Optional[List[str]] = None,
        recovery_attempts: Optional[List[Dict[str, Any]]] = None,
        final_state: HealthState = HealthState.NEEDS_ATTENTION,
        verified_outcome: str = "",
        resolved: bool = False,
        deduplicate_open: bool = True,
    ) -> IncidentRecord:
        """Create and persist a new operational incident record, linking open incidents when flapping."""
        with self._lock:
            clean_symptoms = [sanitize_content(s) for s in symptoms]
            clean_causes = [sanitize_content(c) for c in (verified_causes or [])]
            clean_hypos = [sanitize_content(h) for h in (hypotheses or [])]
            clean_attempts = self._sanitize(recovery_attempts or [])
            clean_outcome = sanitize_content(verified_outcome)

            # Link to existing unresolved incident for the same component to prevent unbounded spam
            if deduplicate_open and not resolved:
                open_incident: Optional[IncidentRecord] = None
                for inc in reversed(list(self._in_memory_incidents.values())):
                    if inc.component_id == component_id and not inc.resolved:
                        open_incident = inc
                        break

                if open_incident is not None:
                    for s in clean_symptoms:
                        if s not in open_incident.symptoms:
                            open_incident.symptoms.append(s)
                    for c in clean_causes:
                        if c not in open_incident.verified_causes:
                            open_incident.verified_causes.append(c)
                    for h in clean_hypos:
                        if h not in open_incident.hypotheses:
                            open_incident.hypotheses.append(h)
                    open_incident.recovery_attempts.extend(clean_attempts)
                    open_incident.final_state = final_state
                    open_incident.occurrence_count += 1
                    if clean_outcome:
                        open_incident.verified_outcome = clean_outcome

                    if self.memory_store is not None:
                        try:
                            summary_content = (
                                f"Operational Incident [{open_incident.incident_id}] for {category.value}:{component_id} - "
                                f"Causes: {', '.join(open_incident.verified_causes) or 'Unverified'}. "
                                f"Occurrences: {open_incident.occurrence_count}. Outcome: {clean_outcome or final_state.value}"
                            )
                            mem_record = MemoryRecord(
                                type=MemoryType.ENVIRONMENT_FACT,
                                content=summary_content,
                                source=MemorySource.SYSTEM,
                                metadata=open_incident.to_dict(),
                            )
                            self.memory_store.add(mem_record)
                        except Exception as ex:
                            logger.warning(f"Failed to update linked incident in memory store: {ex}")

                    self.event_bus.emit(
                        "incident.updated",
                        component_id,
                        data=open_incident.to_dict(),
                    )
                    return open_incident

            # Create new incident record
            inc_id = f"inc_{component_id.replace(':', '_')}_{uuid.uuid4().hex[:8]}"
            now = time.time()
            record = IncidentRecord(
                incident_id=inc_id,
                component_id=component_id,
                category=category,
                symptoms=clean_symptoms,
                verified_causes=clean_causes,
                hypotheses=clean_hypos,
                recovery_attempts=clean_attempts,
                final_state=final_state,
                verified_outcome=clean_outcome,
                resolved=resolved,
                created_at=now,
                resolved_at=now if resolved else None,
                occurrence_count=1,
            )

            self._in_memory_incidents[inc_id] = record

            # Persist to SqliteMemoryStore if available
            if self.memory_store is not None:
                try:
                    summary_content = (
                        f"Operational Incident [{inc_id}] for {category.value}:{component_id} - "
                        f"Causes: {', '.join(clean_causes) or 'Unverified'}. "
                        f"Outcome: {clean_outcome or final_state.value}"
                    )
                    mem_record = MemoryRecord(
                        type=MemoryType.ENVIRONMENT_FACT,
                        content=summary_content,
                        source=MemorySource.SYSTEM,
                        metadata=record.to_dict(),
                    )
                    self.memory_store.add(mem_record)
                except Exception as ex:
                    logger.warning(f"Failed to persist incident record to memory store: {ex}")

            # Emit bus event
            self.event_bus.emit(
                "incident.recorded",
                component_id,
                data=record.to_dict(),
            )
            return record

    def resolve_incident(
        self,
        incident_id: str,
        verified_outcome: str,
        final_state: HealthState = HealthState.HEALTHY,
    ) -> Optional[IncidentRecord]:
        """Mark an existing incident as resolved with verified outcome."""
        with self._lock:
            rec = self._in_memory_incidents.get(incident_id)
            if not rec:
                return None

            rec.resolved = True
            rec.final_state = final_state
            rec.verified_outcome = sanitize_content(verified_outcome)
            rec.resolved_at = time.time()

            # Update in MemoryStore if possible
            if self.memory_store is not None:
                try:
                    mem_record = MemoryRecord(
                        type=MemoryType.ENVIRONMENT_FACT,
                        content=f"Resolved Operational Incident [{incident_id}] for {rec.component_id}: {rec.verified_outcome}",
                        source=MemorySource.SYSTEM,
                        metadata=rec.to_dict(),
                    )
                    self.memory_store.add(mem_record)
                except Exception:
                    pass

            self.event_bus.emit(
                "incident.resolved",
                rec.component_id,
                data=rec.to_dict(),
            )
            return rec

    def get_incident(self, incident_id: str) -> Optional[IncidentRecord]:
        """Fetch incident by ID."""
        with self._lock:
            return self._in_memory_incidents.get(incident_id)

    def list_incidents(
        self,
        component_id: Optional[str] = None,
        resolved: Optional[bool] = None,
        limit: int = 50,
    ) -> List[IncidentRecord]:
        """List in-memory incident records with optional filters."""
        with self._lock:
            recs = list(self._in_memory_incidents.values())
            if component_id:
                recs = [r for r in recs if r.component_id == component_id]
            if resolved is not None:
                recs = [r for r in recs if r.resolved == resolved]
            recs.sort(key=lambda r: r.created_at, reverse=True)
            return recs[:limit]

    def count_recent_failures(
        self,
        component_id: str,
        window_seconds: float = 300.0,
    ) -> int:
        """Count incident occurrences for a component within a trailing time window."""
        with self._lock:
            cutoff = time.time() - window_seconds
            return sum(
                getattr(r, "occurrence_count", 1)
                for r in self._in_memory_incidents.values()
                if r.component_id == component_id and r.created_at >= cutoff
            )
