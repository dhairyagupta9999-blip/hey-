"""Event Rule Engine, Conditional Evaluation, and Loop Safety for Hey Jev.

Coordinates reactive intelligence:
EVENT -> RULE -> (CONDITIONS) -> ACTION (TASK / MISSION / SKILL / NOTIFY)

Loop Safety:
- Event fingerprinting and deduplication
- Cooldown periods and max fire counts
- Strict call-chain depth bounding (max_event_chain_depth = 5)
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from event_bus import EventBus, TaskEvent


class EventActionType(str, Enum):
    """Safe actions executable when an event rule fires."""
    CREATE_TASK = "CREATE_TASK"
    CREATE_BACKGROUND_TASK = "CREATE_TASK"
    CREATE_MISSION = "CREATE_MISSION"
    RUN_SKILL = "RUN_SKILL"
    NOTIFY_USER = "NOTIFY_USER"
    SEND_NOTIFICATION = "NOTIFY_USER"
    DISPATCH_EVENT = "CREATE_TASK"
    RECORD_MEMORY = "RECORD_MEMORY"
    PAUSE_TASK = "PAUSE_TASK"
    RESUME_TASK = "RESUME_TASK"


def evaluate_condition(condition: Dict[str, Any], event_data: Dict[str, Any]) -> bool:
    """Evaluate a single deterministic predicate against event payload."""
    if "field" not in condition:
        for fld, spec in condition.items():
            if isinstance(spec, dict):
                for op_name, expected_val in spec.items():
                    if not evaluate_condition({"field": fld, "op": op_name, "value": expected_val}, event_data):
                        return False
                return True
            else:
                return evaluate_condition({"field": fld, "op": "equals", "value": spec}, event_data)
        return True

    field_name = condition.get("field")
    op = condition.get("op", "equals").lower()
    expected = condition.get("value")

    # If field is not specified, evaluate root event data
    val = event_data.get(field_name) if field_name else event_data

    if op == "exists":
        return field_name in event_data and event_data[field_name] is not None
    if op == "not_exists":
        return field_name not in event_data or event_data[field_name] is None

    if val is None:
        return False

    if op == "equals":
        return str(val).lower() == str(expected).lower()
    elif op == "not_equals":
        return str(val).lower() != str(expected).lower()
    elif op == "contains":
        return str(expected).lower() in str(val).lower()
    elif op == "matches":
        try:
            return bool(re.search(str(expected), str(val), re.IGNORECASE))
        except re.error:
            return False
    elif op == "greater_than":
        try:
            return float(val) > float(expected)
        except (ValueError, TypeError):
            return False
    elif op == "less_than":
        try:
            return float(val) < float(expected)
        except (ValueError, TypeError):
            return False
    elif op == "changed":
        old_v = condition.get("previous_value")
        return val != old_v

    return False


@dataclass
class EventRule:
    """Declarative automation rule binding an event pattern to an action."""
    rule_id: str = field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:8]}")
    name: str = ""
    event_pattern: str = "*"             # Wildcard pattern (e.g. 'file.created', 'project.*')
    event_name: Optional[str] = None     # Alias for event_pattern
    conditions: Any = field(default_factory=list)
    action_type: EventActionType = EventActionType.NOTIFY_USER
    action_payload: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    cooldown_sec: float = 0.0           # Seconds before rule may fire again
    max_fires: int = 0                  # 0 = unlimited fires
    fire_count: int = 0
    last_fired_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_name is not None and self.event_pattern == "*":
            self.event_pattern = self.event_name
        elif self.event_pattern != "*" and self.event_name is None:
            self.event_name = self.event_pattern

        if isinstance(self.conditions, dict):
            # Keep dict format or convert
            cond_list = []
            for k, v in self.conditions.items():
                if isinstance(v, dict):
                    for op, val in v.items():
                        cond_list.append({"field": k, "op": op, "value": val})
                else:
                    cond_list.append({"field": k, "op": "equals", "value": v})
            self.conditions = cond_list
        elif not isinstance(self.conditions, list):
            self.conditions = []

        if isinstance(self.action_type, str):
            try:
                self.action_type = EventActionType(self.action_type.upper())
            except ValueError:
                self.action_type = EventActionType.NOTIFY_USER

    def can_fire(self, now: Optional[float] = None) -> bool:
        """Check if rule is enabled, below max_fires, and outside cooldown."""
        if not self.enabled:
            return False
        if self.max_fires > 0 and self.fire_count >= self.max_fires:
            return False
        if self.cooldown_sec <= 0 or self.last_fired_at is None:
            return True
        current_time = now if now is not None else time.time()
        return (current_time - self.last_fired_at) >= self.cooldown_sec

    def record_fire(self, now: Optional[float] = None) -> None:
        """Increment fire count and update timestamp."""
        self.fire_count += 1
        self.last_fired_at = now if now is not None else time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "event_pattern": self.event_pattern,
            "conditions": list(self.conditions),
            "action_type": self.action_type.value,
            "action_payload": dict(self.action_payload),
            "enabled": self.enabled,
            "cooldown_sec": self.cooldown_sec,
            "max_fires": self.max_fires,
            "fire_count": self.fire_count,
            "last_fired_at": self.last_fired_at,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EventRule:
        d = dict(data)
        return cls(**d)


class EventRuleEngine:
    """Evaluates rules against incoming events with strict loop bounding."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        max_event_chain_depth: int = 5,
        action_dispatcher: Optional[Callable[[EventRule, TaskEvent], Any]] = None,
        max_chain_depth: Optional[int] = None,
        semantic_dedup_window_sec: float = 0.5,
    ) -> None:
        self.event_bus = event_bus or EventBus()
        self.max_event_chain_depth = max_chain_depth if max_chain_depth is not None else max_event_chain_depth
        self.action_dispatcher = action_dispatcher
        self.semantic_dedup_window_sec = semantic_dedup_window_sec
        self._lock = threading.RLock()
        self._rules: Dict[str, EventRule] = {}
        self._recent_fingerprints: Dict[str, float] = {}
        self._recent_semantic_keys: Dict[str, float] = {}
        self._subscribed = False

    def delete_rule(self, rule_id: str) -> bool:
        """Alias for unregister_rule."""
        return self.unregister_rule(rule_id)

    def evaluate_event(
        self,
        event_name: str,
        event_payload: Dict[str, Any],
        chain_depth: int = 0,
    ) -> List[Dict[str, Any]]:
        """Convenience method to evaluate a raw event dict directly."""
        payload = dict(event_payload)
        payload["_chain_depth"] = chain_depth
        event = TaskEvent(
            event_type=event_name,
            task_id=payload.get("task_id", f"evt_{uuid.uuid4().hex[:6]}"),
            agent_name="Caller",
            data=payload,
            timestamp=payload.get("timestamp", time.time()),
        )
        return self.handle_event(event)

    def start_listening(self) -> None:
        """Subscribe to all events on the EventBus."""
        with self._lock:
            if not self._subscribed:
                self.event_bus.subscribe("*", self.handle_event)
                self._subscribed = True

    def stop_listening(self) -> None:
        """Unsubscribe from the EventBus."""
        with self._lock:
            if self._subscribed:
                self.event_bus.unsubscribe("*", self.handle_event)
                self._subscribed = False

    def register_rule(self, rule: EventRule) -> None:
        with self._lock:
            self._rules[rule.rule_id] = rule

    def unregister_rule(self, rule_id: str) -> bool:
        with self._lock:
            return self._rules.pop(rule_id, None) is not None

    def get_rule(self, rule_id: str) -> Optional[EventRule]:
        with self._lock:
            return self._rules.get(rule_id)

    def list_rules(self) -> List[EventRule]:
        with self._lock:
            return list(self._rules.values())

    def compute_fingerprint(self, event: TaskEvent) -> str:
        """Compute an event deduplication fingerprint."""
        body = f"{event.event_type}:{event.task_id}:{json.dumps(event.data, sort_keys=True)}"
        return hashlib.md5(body.encode("utf-8")).hexdigest()[:16]

    def compute_semantic_key(self, event: TaskEvent) -> Optional[str]:
        """Extract semantic deduplication key for entity-based events."""
        data = event.data or {}
        target = (
            data.get("file_path")
            or data.get("path")
            or data.get("file")
            or data.get("project_id")
            or data.get("repo_path")
            or data.get("target_id")
            or data.get("background_task_id")
        )
        if target:
            return f"{event.event_type}:{target}"
        return None

    def handle_event(self, event: TaskEvent) -> List[Dict[str, Any]]:
        """Evaluate incoming event against registered rules with dedup and cycle prevention."""
        depth = int(event.data.get("_chain_depth", 0))
        if depth >= self.max_event_chain_depth:
            return []

        causal_rules = list(event.data.get("_causal_rules", []))
        now = time.time()
        fp = self.compute_fingerprint(event)
        sem_key = self.compute_semantic_key(event)

        with self._lock:
            # Exact fingerprint deduplication within 200ms
            if fp in self._recent_fingerprints and (now - self._recent_fingerprints[fp]) < 0.2:
                return []
            self._recent_fingerprints[fp] = now

            # Semantic deduplication if enabled and entity target present
            if sem_key is not None and self.semantic_dedup_window_sec > 0.0:
                if sem_key in self._recent_semantic_keys and (now - self._recent_semantic_keys[sem_key]) < self.semantic_dedup_window_sec:
                    return []
                self._recent_semantic_keys[sem_key] = now

            # Prune old fingerprints
            if len(self._recent_fingerprints) > 1000:
                cutoff = now - 60.0
                self._recent_fingerprints = {k: v for k, v in self._recent_fingerprints.items() if v >= cutoff}
            if len(self._recent_semantic_keys) > 1000:
                cutoff = now - 60.0
                self._recent_semantic_keys = {k: v for k, v in self._recent_semantic_keys.items() if v >= cutoff}

            rules_to_eval = list(self._rules.values())

        fired_actions: List[Dict[str, Any]] = []

        for rule in rules_to_eval:
            # Causal cycle prevention: break loops where rule triggers itself in same chain
            if rule.rule_id in causal_rules:
                continue

            if not rule.can_fire(now):
                continue
            if not fnmatch.fnmatch(event.event_type, rule.event_pattern):
                continue

            # Evaluate conditions
            all_passed = True
            for cond in rule.conditions:
                if not evaluate_condition(cond, event.data):
                    all_passed = False
                    break

            if all_passed:
                with self._lock:
                    rule.record_fire(now)

                next_causal = causal_rules + [rule.rule_id]
                action_res = {
                    "rule_id": rule.rule_id,
                    "action_type": rule.action_type.value,
                    "payload": dict(rule.action_payload),
                    "event_type": event.event_type,
                    "task_id": event.task_id,
                    "chain_depth": depth + 1,
                    "_causal_rules": next_causal,
                }
                fired_actions.append(action_res)

                # Invoke action dispatcher if provided
                if self.action_dispatcher:
                    try:
                        self.action_dispatcher(rule, event)
                    except Exception:
                        pass

                self.event_bus.emit(
                    event_type="rule.fired",
                    task_id=rule.rule_id,
                    agent_name="EventRuleEngine",
                    data={
                        "action_type": rule.action_type.value,
                        "triggered_by": event.event_type,
                        "chain_depth": depth + 1,
                        "_causal_rules": next_causal,
                    },
                )

        return fired_actions
