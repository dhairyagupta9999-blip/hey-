"""OpenDots Interoperability Gateway for Hey Jev (Phase 29).

Enables secure interoperability between Hey Jev and an optional external
OpenDots workspace via Model Context Protocol (MCP) JSON-RPC 2.0.

Core Architectural Principles:
1. Authority: Hey Jev remains the authoritative agent engine. OpenDots is an optional
   external interface and collaboration space.
2. Disabled by Default: The gateway runs only when explicitly configured and started.
3. Network Boundary: Binds strictly to loopback (127.0.0.1). Rejects non-loopback bindings.
4. Authentication: Requires configured Bearer token authentication via constant-time verification.
   Tokens are never hardcoded and never logged.
5. Safety Invariants:
   - No generic tool/shell execution endpoints.
   - External callers cannot mutate approvals directly; human approval checkpoints are preserved.
   - Previews never execute or persist tasks (zero side effects).
   - Submissions pass through native MissionCoordinator/SqliteMissionStore governance.
   - All output is scrubbed with sanitize_content to prevent secret leakage.
"""
from __future__ import annotations

import collections
import enum
import http.server
import json
import logging
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# Guarded psutil import
try:
    import psutil
except ImportError:
    psutil = None

from memory_model import sanitize_content
from mission_coordinator import MissionCoordinator
from mission_interpreter import MissionInterpreter
from mission_model import (
    AcceptanceCriterion,
    AcceptanceStatus,
    Mission,
    MissionBudget,
    MissionMode,
    MissionPlan,
    MissionStatus,
    MissionTask,
    MissionTaskStatus,
)
from mission_planner import MissionPlanner
from mission_store import SqliteMissionStore
from operational_health import HealthObservation, HealthState, OperationalHealthCoordinator
from capability_registry import CapabilityRegistry
from resource_governor import ResourceGovernor
from tool_model import RiskLevel

logger = logging.getLogger("hey_jev.opendots_gateway")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class OpenDotsGatewayConfig:
    """Configuration for OpenDots Interoperability Gateway."""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    bearer_token: Optional[str] = None
    max_request_bytes: int = 1_048_576  # 1 MB
    max_timeline_events: int = 200
    max_concurrency: int = 8

    def validate(self) -> None:
        """Validate configuration constraints and fail-safe security invariants."""
        # 1. Loopback invariant
        if self.host not in ("127.0.0.1", "localhost"):
            raise ValueError(
                f"Security violation: OpenDots gateway must bind to loopback (127.0.0.1) only; "
                f"attempted to bind to '{self.host}'."
            )
        # 2. Token requirement when enabled
        if self.enabled:
            if not self.bearer_token or not self.bearer_token.strip():
                raise ValueError(
                    "Security violation: OpenDots gateway cannot be enabled without a valid "
                    "configured bearer token (JEV_OPENDOTS_BEARER_TOKEN)."
                )

    @classmethod
    def from_env(cls) -> OpenDotsGatewayConfig:
        """Construct config from environment variables."""
        enabled_str = os.environ.get("JEV_ENABLE_OPENDOTS_GATEWAY", "0").strip().lower()
        enabled = enabled_str in ("1", "true", "yes", "on")

        port_str = os.environ.get("JEV_OPENDOTS_PORT", "8765").strip()
        try:
            port = int(port_str)
        except ValueError:
            port = 8765

        concurrency_str = os.environ.get("JEV_OPENDOTS_MAX_CONCURRENCY", "8").strip()
        try:
            max_concurrency = int(concurrency_str)
        except ValueError:
            max_concurrency = 8

        host = os.environ.get("JEV_OPENDOTS_HOST", "127.0.0.1").strip() or "127.0.0.1"
        token = os.environ.get("JEV_OPENDOTS_BEARER_TOKEN", "").strip() or None

        cfg = cls(
            enabled=enabled,
            host=host,
            port=port,
            bearer_token=token,
            max_concurrency=max_concurrency,
        )
        return cfg


# ---------------------------------------------------------------------------
# Gateway Tool Definitions (MCP Schema)
# ---------------------------------------------------------------------------

MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "jev_health",
        "description": "Retrieve Hey Jev subsystem operational health, memory footprint, CPU load, and resource governor slot occupancy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Optional subsystem category filter (e.g. RUNTIME_COMPONENT, BACKGROUND_TASK, PROVIDER).",
                }
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "title": "Subsystem Operational Health",
        },
    },
    {
        "name": "jev_list_missions",
        "description": "List missions with safe summary metadata and derived progress percentage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Optional status filter (e.g. READY, RUNNING, WAITING_FOR_APPROVAL, COMPLETED, PAUSED, CANCELLED).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of records to return (default 20, max 100).",
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset (default 0).",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "title": "List Mission Summaries",
        },
    },
    {
        "name": "jev_get_mission",
        "description": "Retrieve comprehensive mission details including plan DAG, task verification records, and acceptance criteria.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {
                    "type": "string",
                    "description": "Unique identifier of the mission to inspect.",
                }
            },
            "required": ["mission_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "title": "Get Detailed Mission Plan",
        },
    },
    {
        "name": "jev_get_mission_timeline",
        "description": "Retrieve chronological, deduplicated lifecycle events for a specific mission.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {
                    "type": "string",
                    "description": "Mission ID to inspect.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of events to return (default 50, max 100).",
                },
            },
            "required": ["mission_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "title": "Get Mission Lifecycle Timeline",
        },
    },
    {
        "name": "jev_search_capabilities",
        "description": "Search available outcome-oriented capabilities (tools and skills) without executing them.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to match against capability ID, name, or description.",
                },
                "capability_type": {
                    "type": "string",
                    "description": "Optional type filter ('TOOL' or 'SKILL').",
                },
                "max_risk": {
                    "type": "string",
                    "description": "Maximum risk level ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL').",
                },
            },
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "title": "Search System Capabilities",
        },
    },
    {
        "name": "jev_create_mission_preview",
        "description": "Interpret a natural-language goal and generate a draft plan DAG without executing or persisting anything (zero side-effects).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "Natural-language mission objective or user request.",
                },
                "mode": {
                    "type": "string",
                    "description": "Operational mode ('MANUAL', 'COPILOT', 'AUTOPILOT'). Default 'COPILOT'.",
                },
                "context": {
                    "type": "object",
                    "description": "Optional contextual parameters.",
                },
            },
            "required": ["objective"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "title": "Preview Mission Plan",
        },
    },
    {
        "name": "jev_submit_mission",
        "description": "Submit a goal into Jev's persistent mission lifecycle. Enforces human approval checkpoints and mode constraints.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "Mission objective to execute.",
                },
                "mode": {
                    "type": "string",
                    "description": "Operational mode ('MANUAL', 'COPILOT', 'AUTOPILOT'). Default 'COPILOT'.",
                },
                "context": {
                    "type": "object",
                    "description": "Optional contextual parameters.",
                },
                "auto_execute": {
                    "type": "boolean",
                    "description": "If True and mode is AUTOPILOT and no approvals required, begin background execution. Otherwise stays in READY or WAITING_FOR_APPROVAL.",
                },
            },
            "required": ["objective"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "title": "Submit Mission Objective",
        },
    },
    {
        "name": "jev_request_pause",
        "description": "Request pausing of an active mission.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {
                    "type": "string",
                    "description": "ID of the mission to pause.",
                }
            },
            "required": ["mission_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "title": "Pause Mission Execution",
        },
    },
    {
        "name": "jev_request_resume",
        "description": "Request resumption of a paused mission.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {
                    "type": "string",
                    "description": "ID of the mission to resume.",
                }
            },
            "required": ["mission_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "title": "Resume Mission Execution",
        },
    },
    {
        "name": "jev_request_cancellation",
        "description": "Request cancellation of a mission, stopping in-flight tasks and preserving completed verification evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": {
                    "type": "string",
                    "description": "ID of the mission to cancel.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional reason for cancellation.",
                },
            },
            "required": ["mission_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "title": "Cancel Mission Execution",
        },
    },
]


# ---------------------------------------------------------------------------
# Core MCP Gateway Logic
# ---------------------------------------------------------------------------

class OpenDotsMCPGateway:
    """Authoritative MCP JSON-RPC 2.0 Gateway connecting OpenDots to Hey Jev."""

    def __init__(
        self,
        config: OpenDotsGatewayConfig,
        mission_store: Optional[SqliteMissionStore] = None,
        coordinator: Optional[MissionCoordinator] = None,
        interpreter: Optional[MissionInterpreter] = None,
        planner: Optional[MissionPlanner] = None,
        capability_registry: Optional[CapabilityRegistry] = None,
        health_coordinator: Optional[OperationalHealthCoordinator] = None,
        resource_governor: Optional[ResourceGovernor] = None,
    ) -> None:
        self.config = config
        self.store = mission_store or SqliteMissionStore()
        self.registry = capability_registry or CapabilityRegistry()
        self.interpreter = interpreter or MissionInterpreter(registry=self.registry)
        self.planner = planner or MissionPlanner(registry=self.registry, resource_governor=resource_governor)
        self.coordinator = coordinator
        self.health_coordinator = health_coordinator
        self.resource_governor = resource_governor

        self._lock = threading.RLock()
        self._timeline_events: Dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=self.config.max_timeline_events)
        )
        self._seen_event_fingerprints: Set[Tuple[str, str, float]] = set()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._latest_session_id: Optional[str] = None

        # Connect to coordinator EventBus if available
        if self.coordinator and getattr(self.coordinator, "event_bus", None):
            self._subscribe_events(self.coordinator.event_bus)

    @property
    def latest_session_id(self) -> Optional[str]:
        with self._lock:
            return self._latest_session_id

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions

    def terminate_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def _subscribe_events(self, event_bus: Any) -> None:
        """Subscribe to mission events to maintain an in-memory chronological timeline."""
        try:
            if hasattr(event_bus, "subscribe"):
                event_bus.subscribe("*", self._on_bus_event)
        except Exception as e:
            logger.debug("Could not subscribe to EventBus: %s", e)

    def _on_bus_event(self, event: Any) -> None:
        """Capture and deduplicate mission events for timeline retrieval."""
        event_type = getattr(event, "event_type", "")
        if not event_type.startswith("mission."):
            return

        mission_id = getattr(event, "task_id", "")
        if not mission_id:
            return

        ts = getattr(event, "timestamp", time.time())
        fingerprint = (event_type, mission_id, round(ts, 2))
        with self._lock:
            if fingerprint in self._seen_event_fingerprints:
                return
            self._seen_event_fingerprints.add(fingerprint)
            clean_data = self._sanitize_deep(getattr(event, "data", {}) or {})
            self._timeline_events[mission_id].append({
                "timestamp": ts,
                "event_type": event_type,
                "data": clean_data,
            })

    def authenticate_token(self, provided_token: Optional[str]) -> bool:
        """Verify provided bearer token using constant-time comparison. Never logs the token."""
        if not self.config.bearer_token:
            return False
        if not provided_token:
            return False
        return secrets.compare_digest(provided_token.strip(), self.config.bearer_token.strip())

    # -----------------------------------------------------------------------
    # JSON-RPC 2.0 Handler
    # -----------------------------------------------------------------------

    SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2024-11-05"]
    DEFAULT_PROTOCOL_VERSION = "2025-03-26"

    def handle_jsonrpc(self, request: Dict[str, Any], session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Process a single JSON-RPC 2.0 message conforming strictly to the MCP specification."""
        if not isinstance(request, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request: expected JSON object."},
            }

        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        # Handle notifications (requests with no 'id')
        is_notification = ("id" not in request or req_id is None) and (
            method == "notifications/initialized"
            or (isinstance(method, str) and method.startswith("notifications/"))
        )
        if is_notification:
            logger.debug("Received MCP notification: %s", method)
            return None

        if not method or not isinstance(method, str):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32600, "message": "Invalid Request: missing or invalid method."},
            }

        try:
            if method == "initialize":
                return self._handle_initialize(req_id, params, session_id=session_id)
            elif method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            elif method == "tools/list":
                return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
            elif method == "tools/call":
                return self._handle_tools_call(req_id, params)
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{sanitize_content(method)}' not found.",
                    },
                }
        except Exception as e:
            logger.error("Unhandled error processing JSON-RPC method %s: %s", method, e, exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"Internal server error: {sanitize_content(str(e))}",
                },
            }

    def _handle_initialize(self, req_id: Any, params: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
        """Respond to MCP initialize handshake with protocol version negotiation and session registration."""
        requested_version = params.get("protocolVersion")
        if requested_version in self.SUPPORTED_PROTOCOL_VERSIONS:
            negotiated_version = requested_version
        else:
            negotiated_version = self.DEFAULT_PROTOCOL_VERSION

        new_session_id = session_id or str(uuid.uuid4())
        with self._lock:
            self._sessions[new_session_id] = {
                "created_at": time.time(),
                "protocol_version": negotiated_version,
                "client_info": params.get("clientInfo", {}),
            }
            self._latest_session_id = new_session_id

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": negotiated_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "hey-jev-gateway",
                    "version": "1.0.0",
                },
            },
        }

    def _handle_tools_call(self, req_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch tool calls and format MCP text content result."""
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Invalid params: expected object."},
            }

        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            arguments = {}

        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing tool name in tools/call."},
            }

        # Dispatch to tool executor
        success, result_data, error_msg = self.execute_tool(tool_name, arguments)

        if not success:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [
                        {
                            "type": "text",
                            "text": sanitize_content(error_msg or "Unknown tool execution error."),
                        }
                    ],
                },
            }

        # Format successful output as JSON text content
        json_output = json.dumps(self._sanitize_deep(result_data), indent=2)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json_output,
                    }
                ],
            },
        }

    # -----------------------------------------------------------------------
    # Tool Execution Implementation
    # -----------------------------------------------------------------------

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Dispatch tool call to appropriate handler. Returns (success, result_data, error_msg)."""
        handlers = {
            "jev_health": self._tool_health,
            "jev_list_missions": self._tool_list_missions,
            "jev_get_mission": self._tool_get_mission,
            "jev_get_mission_timeline": self._tool_get_mission_timeline,
            "jev_search_capabilities": self._tool_search_capabilities,
            "jev_create_mission_preview": self._tool_create_mission_preview,
            "jev_submit_mission": self._tool_submit_mission,
            "jev_request_pause": self._tool_request_pause,
            "jev_request_resume": self._tool_request_resume,
            "jev_request_cancellation": self._tool_request_cancellation,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return False, None, f"Tool '{tool_name}' is not recognized or not authorized."

        try:
            return handler(arguments)
        except Exception as e:
            logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
            return False, None, f"Tool execution failed: {str(e)}"

    def _tool_health(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Read-only tool: Subsystem health, memory, CPU, and governor slot occupancy."""
        category_filter = args.get("category")
        subsystems_data: List[Dict[str, Any]] = []

        if self.health_coordinator:
            obs_list = self.health_coordinator.get_all_health()
            for obs in obs_list:
                if category_filter and obs.category.value != category_filter:
                    continue
                subsystems_data.append(obs.to_dict())

        # Governor slots
        governor_info: Dict[str, Any] = {
            "configured": self.resource_governor is not None,
        }
        if self.resource_governor:
            with self.resource_governor._lock:
                governor_info.update({
                    "foreground_active": len(self.resource_governor._active_foreground),
                    "foreground_max": self.resource_governor.foreground_slots,
                    "background_active": len(self.resource_governor._active_background),
                    "background_max": self.resource_governor.background_slots,
                    "voice_active": self.resource_governor._voice_active,
                })

        # Memory & CPU metrics (safe bounded metrics, no secrets)
        metrics: Dict[str, Any] = {}
        if psutil:
            try:
                vm = psutil.virtual_memory()
                metrics["memory"] = {
                    "total_mb": round(vm.total / (1024 * 1024), 1),
                    "available_mb": round(vm.available / (1024 * 1024), 1),
                    "used_mb": round(vm.used / (1024 * 1024), 1),
                    "percent": vm.percent,
                }
                metrics["cpu_percent"] = psutil.cpu_percent(interval=None)
            except Exception as e:
                metrics["error"] = str(e)

        result = {
            "status": "healthy" if not any(s.get("state") in ("UNAVAILABLE", "NEEDS_ATTENTION") for s in subsystems_data) else "degraded",
            "subsystems": subsystems_data,
            "governor": governor_info,
            "system_metrics": metrics,
            "timestamp": time.time(),
        }
        return True, result, None

    def _tool_list_missions(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Read-only tool: List missions with safe summaries and derived progress."""
        status_filter_str = args.get("status")
        status_enum = None
        if status_filter_str:
            try:
                status_enum = MissionStatus(status_filter_str.upper())
            except ValueError:
                return False, None, f"Invalid status filter '{status_filter_str}'."

        limit = max(1, min(100, int(args.get("limit", 20))))
        offset = max(0, int(args.get("offset", 0)))

        all_missions = self.store.list_missions(status=status_enum)
        paged_missions = all_missions[offset : offset + limit]

        summaries = []
        for m in paged_missions:
            total_tasks = len(m.plan.tasks) if m.plan else 0
            completed_tasks = sum(1 for t in m.plan.tasks if t.status == MissionTaskStatus.COMPLETED) if m.plan else 0
            progress = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else (100.0 if m.status == MissionStatus.COMPLETED else 0.0)

            summaries.append({
                "mission_id": m.mission_id,
                "objective": sanitize_content(m.objective),
                "mode": m.mode.value,
                "status": m.status.value,
                "status_reason": sanitize_content(m.status_reason),
                "progress_percent": round(progress, 1),
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "requires_approval": (m.status == MissionStatus.WAITING_FOR_APPROVAL),
            })

        return True, {
            "total_count": len(all_missions),
            "offset": offset,
            "limit": limit,
            "missions": summaries,
        }, None

    def _tool_get_mission(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Read-only tool: Comprehensive mission inspection."""
        mission_id = args.get("mission_id")
        if not mission_id:
            return False, None, "Missing required argument 'mission_id'."

        mission = self.store.get_mission(mission_id)
        if not mission:
            return False, None, f"Mission '{mission_id}' not found."

        total_tasks = len(mission.plan.tasks) if mission.plan else 0
        completed_tasks = sum(1 for t in mission.plan.tasks if t.status == MissionTaskStatus.COMPLETED) if mission.plan else 0
        progress = (completed_tasks / total_tasks * 100.0) if total_tasks > 0 else (100.0 if mission.status == MissionStatus.COMPLETED else 0.0)

        tasks_detail = []
        if mission.plan:
            for t in mission.plan.tasks:
                task_desc = getattr(t, "description", getattr(t, "title", ""))
                tasks_detail.append({
                    "task_id": t.task_id,
                    "description": sanitize_content(task_desc),
                    "intended_result": sanitize_content(getattr(t, "intended_result", "")),
                    "capability_id": t.capability_id,
                    "status": t.status.value,
                    "risk_level": t.risk_level.value,
                    "approval_checkpoint": t.approval_checkpoint,
                    "approval_reason": sanitize_content(t.approval_reason),
                    "dependencies": t.dependencies,
                    "error": sanitize_content(t.error or ""),
                    "retry_count": t.retry_count,
                    "is_idempotent": t.is_idempotent,
                })

        criteria_detail = []
        for ac in mission.acceptance_criteria:
            criteria_detail.append({
                "criterion_id": ac.criterion_id,
                "description": sanitize_content(ac.description),
                "status": ac.status.value,
                "required": ac.required,
                "verified_at": ac.verified_at,
                "evidence": self._sanitize_deep(ac.evidence or {}),
                "external_dependency": ac.external_dependency,
            })

        detail = {
            "mission_id": mission.mission_id,
            "objective": sanitize_content(mission.objective),
            "normalized_goal": sanitize_content(mission.normalized_goal),
            "mode": mission.mode.value,
            "status": mission.status.value,
            "status_reason": sanitize_content(mission.status_reason),
            "progress_percent": round(progress, 1),
            "created_at": mission.created_at,
            "updated_at": mission.updated_at,
            "plan_version": mission.plan.plan_version if mission.plan else 0,
            "approved_plan_version": mission.approved_plan_version,
            "approved_tasks": list(mission.approved_tasks),
            "tasks": tasks_detail,
            "acceptance_criteria": criteria_detail,
            "blockers": [sanitize_content(b) for b in mission.blockers],
            "assumptions": [sanitize_content(a) for a in mission.assumptions],
            "final_report": self._sanitize_deep(mission.final_report or {}),
        }
        return True, detail, None

    def _tool_get_mission_timeline(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Read-only tool: Chronological lifecycle events."""
        mission_id = args.get("mission_id")
        if not mission_id:
            return False, None, "Missing required argument 'mission_id'."

        limit = max(1, min(100, int(args.get("limit", 50))))
        events = []
        with self._lock:
            event_queue = self._timeline_events.get(mission_id, collections.deque())
            events.extend(list(event_queue))

        # Also query coordinator event bus history if available
        if self.coordinator and getattr(self.coordinator, "event_bus", None):
            try:
                bus_events = self.coordinator.event_bus.get_history(task_id=mission_id)
                for be in bus_events:
                    events.append({
                        "timestamp": be.timestamp,
                        "event_type": be.event_type,
                        "data": self._sanitize_deep(be.data or {}),
                    })
            except Exception as e:
                logger.debug("Could not query EventBus history: %s", e)

        # Deduplicate by fingerprint (round timestamp, event_type)
        deduped = []
        seen = set()
        for ev in events:
            fp = (round(ev["timestamp"], 2), ev["event_type"])
            if fp not in seen:
                seen.add(fp)
                deduped.append(ev)

        deduped.sort(key=lambda x: x["timestamp"])
        trimmed = deduped[-limit:]

        return True, {
            "mission_id": mission_id,
            "event_count": len(trimmed),
            "events": trimmed,
        }, None

    def _tool_search_capabilities(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Read-only tool: Pure capability discovery without execution."""
        query = (args.get("query") or "").strip().lower()
        cap_type_str = args.get("capability_type")
        max_risk_str = args.get("max_risk")

        max_risk = None
        if max_risk_str:
            try:
                max_risk = RiskLevel(max_risk_str.upper())
            except ValueError:
                pass

        capabilities = self.registry.list(max_risk=max_risk, only_available=False)
        matched = []
        for cap in capabilities:
            if cap_type_str and cap.capability_type.value != cap_type_str.upper():
                continue

            if query:
                haystack = f"{cap.capability_id} {cap.name} {cap.description}".lower()
                if query not in haystack:
                    continue

            matched.append({
                "capability_id": cap.capability_id,
                "name": sanitize_content(cap.name),
                "description": sanitize_content(cap.description),
                "capability_type": cap.capability_type.value,
                "risk_level": cap.risk_level.value,
                "is_available": cap.is_available,
                "permissions": [p.value for p in cap.permissions],
            })

        return True, {
            "query": query,
            "total_matches": len(matched),
            "capabilities": matched,
        }, None

    def _tool_create_mission_preview(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Controlled tool: Side-effect-free draft plan preview. Does NOT execute or persist."""
        objective = (args.get("objective") or "").strip()
        if not objective:
            return False, None, "Missing required argument 'objective'."

        mode_str = (args.get("mode") or "COPILOT").upper()
        try:
            mode = MissionMode(mode_str)
        except ValueError:
            return False, None, f"Invalid mode '{mode_str}'. Must be MANUAL, COPILOT, or AUTOPILOT."

        context = args.get("context") or {}

        # 1. Interpret objective into in-memory mission
        mission, meta = self.interpreter.interpret_objective(
            objective=objective,
            context=context,
            mode=mode,
        )

        # 2. Generate DAG plan in-memory
        plan = self.planner.create_plan(mission)
        mission.plan = plan

        # Strict Invariant: DO NOT SAVE TO DATABASE, DO NOT EXECUTE
        planned_tasks = [
            {
                "task_id": t.task_id,
                "description": sanitize_content(getattr(t, "description", getattr(t, "title", ""))),
                "intended_result": sanitize_content(getattr(t, "intended_result", "")),
                "capability_id": t.capability_id,
                "risk_level": t.risk_level.value,
                "approval_checkpoint": t.approval_checkpoint,
                "approval_reason": sanitize_content(t.approval_reason),
                "dependencies": t.dependencies,
            }
            for t in plan.tasks
        ]

        criteria = [
            {
                "criterion_id": ac.criterion_id,
                "description": sanitize_content(ac.description),
                "required": ac.required,
            }
            for ac in mission.acceptance_criteria
        ]

        preview = {
            "objective": sanitize_content(objective),
            "normalized_goal": sanitize_content(mission.normalized_goal),
            "mode": mission.mode.value,
            "explicit_constraints": [sanitize_content(c) for c in mission.explicit_constraints],
            "inferred_constraints": [sanitize_content(c) for c in mission.inferred_constraints],
            "acceptance_criteria": criteria,
            "planned_tasks": planned_tasks,
            "total_tasks": len(planned_tasks),
            "checkpoints_count": sum(1 for t in planned_tasks if t["approval_checkpoint"]),
            "preview_only": True,
            "persisted": False,
            "executed": False,
        }
        return True, preview, None

    def _tool_submit_mission(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Controlled tool: Submit mission through authoritative lifecycle."""
        objective = (args.get("objective") or "").strip()
        if not objective:
            return False, None, "Missing required argument 'objective'."

        mode_str = (args.get("mode") or "COPILOT").upper()
        try:
            mode = MissionMode(mode_str)
        except ValueError:
            return False, None, f"Invalid mode '{mode_str}'. Must be MANUAL, COPILOT, or AUTOPILOT."

        context = args.get("context") or {}
        auto_execute = bool(args.get("auto_execute", False))

        # 1. Interpret and plan
        mission, meta = self.interpreter.interpret_objective(
            objective=objective,
            context=context,
            mode=mode,
        )
        plan = self.planner.create_plan(mission)
        mission.plan = plan

        # 2. Check approval requirements
        has_approval_checkpoints = any(
            t.approval_checkpoint or t.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
            for t in plan.tasks
        )
        requires_approval = has_approval_checkpoints or (mode in (MissionMode.MANUAL, MissionMode.COPILOT))

        if requires_approval:
            mission.status = MissionStatus.WAITING_FOR_APPROVAL
            mission.status_reason = "Mission submitted; awaiting human approval of plan and checkpoints."
        else:
            mission.status = MissionStatus.READY
            mission.status_reason = "Mission submitted and ready for execution."

        # 3. Persist to authoritative SQLite store
        self.store.save_mission(mission)

        # 4. Handle auto_execute safely
        executed = False
        if auto_execute:
            if not requires_approval and mode == MissionMode.AUTOPILOT and self.coordinator:
                # Safe to launch execution thread
                threading.Thread(
                    target=self.coordinator.execute_mission,
                    args=(mission.mission_id,),
                    daemon=True,
                ).start()
                executed = True
            else:
                logger.info(
                    "Auto-execution suppressed for mission %s (mode=%s, requires_approval=%s)",
                    mission.mission_id, mode.value, requires_approval
                )

        return True, {
            "mission_id": mission.mission_id,
            "status": mission.status.value,
            "status_reason": sanitize_content(mission.status_reason),
            "mode": mission.mode.value,
            "plan_version": plan.plan_version,
            "total_tasks": len(plan.tasks),
            "requires_approval": requires_approval,
            "persisted": True,
            "executed": executed,
        }, None

    def _tool_request_pause(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Controlled tool: Request mission pause."""
        mission_id = args.get("mission_id")
        if not mission_id:
            return False, None, "Missing required argument 'mission_id'."

        if not self.coordinator:
            return False, None, "MissionCoordinator not attached to gateway."

        success = self.coordinator.pause_mission(mission_id)
        mission = self.store.get_mission(mission_id)
        current_status = mission.status.value if mission else "UNKNOWN"

        return True, {
            "success": success,
            "mission_id": mission_id,
            "status": current_status,
        }, None

    def _tool_request_resume(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Controlled tool: Request mission resumption."""
        mission_id = args.get("mission_id")
        if not mission_id:
            return False, None, "Missing required argument 'mission_id'."

        if not self.coordinator:
            return False, None, "MissionCoordinator not attached to gateway."

        success = self.coordinator.resume_mission(mission_id)
        mission = self.store.get_mission(mission_id)
        current_status = mission.status.value if mission else "UNKNOWN"

        return True, {
            "success": success,
            "mission_id": mission_id,
            "status": current_status,
        }, None

    def _tool_request_cancellation(self, args: Dict[str, Any]) -> Tuple[bool, Any, Optional[str]]:
        """Controlled tool: Request mission cancellation."""
        mission_id = args.get("mission_id")
        if not mission_id:
            return False, None, "Missing required argument 'mission_id'."

        if not self.coordinator:
            return False, None, "MissionCoordinator not attached to gateway."

        reason = sanitize_content(args.get("reason") or "Cancelled via OpenDots gateway.")
        success = self.coordinator.cancel_mission(mission_id, reason=reason)
        mission = self.store.get_mission(mission_id)
        current_status = mission.status.value if mission else "UNKNOWN"

        return True, {
            "success": success,
            "mission_id": mission_id,
            "status": current_status,
        }, None

    def _sanitize_deep(self, data: Any) -> Any:
        """Recursively scrub sensitive information from any data structure."""
        if isinstance(data, str):
            return sanitize_content(data)
        elif isinstance(data, dict):
            return {self._sanitize_deep(k): self._sanitize_deep(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._sanitize_deep(item) for item in data]
        elif isinstance(data, tuple):
            return tuple(self._sanitize_deep(item) for item in data)
        elif isinstance(data, set):
            return {self._sanitize_deep(item) for item in data}
        return data


# ---------------------------------------------------------------------------
# Loopback HTTP / MCP Server Implementation
# ---------------------------------------------------------------------------

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server with immediate thread cleanup and daemon threads."""
    daemon_threads = True
    allow_reuse_address = True


class OpenDotsHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler enforcing Bearer auth, loopback host/origin checks, and routing Streamable HTTP MCP."""

    server: OpenDotsHTTPServer  # Type hint

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging; sanitize and log to python logger."""
        # Never log auth headers or tokens
        msg = format % args
        logger.debug("[HTTP Gateway] %s - %s", self.address_string(), sanitize_content(msg))

    def _is_loopback_host(self, host_str: str) -> bool:
        """Check if Host header points strictly to loopback."""
        if not host_str:
            return False
        host_str = host_str.strip()
        if host_str.startswith("["):
            bracket_end = host_str.find("]")
            if bracket_end != -1:
                ip = host_str[1:bracket_end].strip()
                return ip in ("::1", "0:0:0:0:0:0:0:1")
        hostname = host_str.split(":")[0].strip().lower()
        return hostname in ("127.0.0.1", "localhost")

    def _drain_body(self) -> None:
        """Drain request body if present to prevent Windows TCP RST on early response/close."""
        cl = self.headers.get("Content-Length")
        if cl:
            try:
                length = int(cl)
                if 0 < length <= self.server.gateway.config.max_request_bytes:
                    self.rfile.read(length)
            except Exception:
                pass

    def _check_host_header(self) -> bool:
        """Enforce strict loopback Host validation to prevent DNS rebinding attacks."""
        host = self.headers.get("Host", "")
        if not self._is_loopback_host(host):
            self._drain_body()
            self._send_json(403, {"error": "Forbidden: invalid Host header for loopback service."})
            return False
        return True

    def _is_loopback_origin(self, origin_str: str) -> bool:
        """Check if Origin header originates from loopback."""
        if not origin_str:
            return True
        try:
            parsed = urllib.parse.urlparse(origin_str)
            hostname = (parsed.hostname or "").lower()
            return hostname in ("127.0.0.1", "localhost", "::1")
        except Exception:
            return False

    def _check_origin_header(self) -> bool:
        """Validate Origin header: reject untrusted external origins; allow loopback."""
        origin = self.headers.get("Origin")
        if origin and not self._is_loopback_origin(origin):
            self._drain_body()
            self._send_json(403, {"error": "Forbidden: untrusted Origin."})
            return False
        return True

    def _set_cors_headers(self) -> None:
        """Apply CORS headers for valid loopback origins."""
        origin = self.headers.get("Origin")
        if origin and self._is_loopback_origin(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, Mcp-Session-Id")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, status_code: int, data: Any, session_id: Optional[str] = None) -> None:
        """Send JSON response with proper headers."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self._set_cors_headers()
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        """Check Authorization header for Bearer token without logging it."""
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return False
        token = auth_header[7:].strip()
        return self.server.gateway.authenticate_token(token)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        if not self.server.concurrency_semaphore.acquire(blocking=False):
            self._send_json(503, {"error": "Service Unavailable: gateway concurrency limit reached."})
            return
        try:
            if not self._check_host_header():
                return
            if not self._check_origin_header():
                return
            self.send_response(204)
            self._set_cors_headers()
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        finally:
            self.server.concurrency_semaphore.release()

    def do_GET(self) -> None:
        """Handle GET requests for health check or ping, and reject /mcp without SSE."""
        if not self.server.concurrency_semaphore.acquire(blocking=False):
            self._send_json(503, {"error": "Service Unavailable: gateway concurrency limit reached."})
            return
        try:
            if not self._check_host_header():
                return
            if not self._check_origin_header():
                return

            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/health", "/ping"):
                self._send_json(200, {
                    "status": "ok",
                    "service": "hey-jev-opendots-gateway",
                    "loopback": True,
                })
                return

            if parsed.path in ("/mcp", "/jsonrpc"):
                # Per MCP Streamable HTTP transport spec:
                # If server receives GET on endpoint that does not support SSE, respond with 405 Method Not Allowed
                self.send_response(405)
                self._set_cors_headers()
                self.send_header("Allow", "POST, DELETE, OPTIONS")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                error_body = json.dumps({
                    "error": "Method Not Allowed: MCP endpoint requires POST for requests or DELETE for session termination."
                }).encode("utf-8")
                self.send_header("Content-Length", str(len(error_body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(error_body)
                return

            self._send_json(404, {"error": "Not Found"})
        finally:
            self.server.concurrency_semaphore.release()

    def do_DELETE(self) -> None:
        """Handle DELETE requests to terminate an MCP session."""
        if not self.server.concurrency_semaphore.acquire(blocking=False):
            self._send_json(503, {"error": "Service Unavailable: gateway concurrency limit reached."})
            return
        try:
            if not self._check_host_header():
                return
            if not self._check_origin_header():
                return

            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/mcp", "/jsonrpc", "/"):
                self._send_json(404, {"error": "Not Found"})
                return

            if not self._check_auth():
                self._send_json(401, {"error": "Unauthorized: missing or invalid bearer token."})
                return

            session_id = self.headers.get("Mcp-Session-Id", "").strip()
            if session_id:
                self.server.gateway.terminate_session(session_id)

            self.send_response(204)
            self._set_cors_headers()
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        finally:
            self.server.concurrency_semaphore.release()

    def do_POST(self) -> None:
        """Handle POST requests for MCP JSON-RPC."""
        if not self.server.concurrency_semaphore.acquire(blocking=False):
            self._drain_body()
            self._send_json(503, {"error": "Service Unavailable: gateway concurrency limit reached."})
            return
        try:
            if not self._check_host_header():
                return
            if not self._check_origin_header():
                return

            parsed = urllib.parse.urlparse(self.path)
            if parsed.path not in ("/mcp", "/jsonrpc", "/"):
                self._drain_body()
                self._send_json(404, {"error": "Not Found"})
                return

            # Enforce Accept Header validation
            accept = self.headers.get("Accept", "").strip().lower()
            if accept:
                acceptable = any(t in accept for t in ("application/json", "text/event-stream", "*/*"))
                if not acceptable:
                    self._drain_body()
                    self._send_json(406, {
                        "error": "Not Acceptable: client must accept application/json or text/event-stream.",
                    })
                    return

            # Enforce Authentication
            if not self._check_auth():
                self._drain_body()
                self._send_json(401, {
                    "error": "Unauthorized: missing or invalid bearer token.",
                })
                return

            # Check payload size
            content_length_header = self.headers.get("Content-Length")
            if not content_length_header:
                self._send_json(411, {"error": "Length Required"})
                return

            try:
                length = int(content_length_header)
            except ValueError:
                self._send_json(400, {"error": "Invalid Content-Length"})
                return

            if length > self.server.gateway.config.max_request_bytes:
                self._send_json(413, {"error": "Payload Too Large"})
                return

            try:
                body_bytes = self.rfile.read(length)
                body_text = body_bytes.decode("utf-8")
                request_data = json.loads(body_text)
            except Exception as e:
                self._send_json(400, {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {str(e)}"},
                })
                return

            session_id = self.headers.get("Mcp-Session-Id", "").strip() or None

            # Handle JSON-RPC Batch
            if isinstance(request_data, list):
                if not request_data:
                    self._send_json(400, {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request: empty batch."},
                    })
                    return

                batch_responses = []
                for item in request_data:
                    res = self.server.gateway.handle_jsonrpc(item, session_id=session_id)
                    if res is not None:
                        batch_responses.append(res)

                active_session_id = session_id or self.server.gateway.latest_session_id
                if not batch_responses:
                    # All were notifications -> 202 Accepted
                    self.send_response(202)
                    self._set_cors_headers()
                    if active_session_id:
                        self.send_header("Mcp-Session-Id", active_session_id)
                    self.send_header("Content-Length", "0")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    return

                self._send_json(200, batch_responses, session_id=active_session_id)
                return

            # Single JSON-RPC Request
            response_data = self.server.gateway.handle_jsonrpc(request_data, session_id=session_id)

            active_session_id = session_id or self.server.gateway.latest_session_id

            if response_data is None:
                # Notification with no response per MCP Streamable HTTP specification: return 202 Accepted
                self.send_response(202)
                self._set_cors_headers()
                if active_session_id:
                    self.send_header("Mcp-Session-Id", active_session_id)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                return

            self._send_json(200, response_data, session_id=active_session_id)
        finally:
            self.server.concurrency_semaphore.release()


class OpenDotsHTTPServer(ThreadedHTTPServer):
    """Custom HTTP Server holding a reference to the OpenDotsMCPGateway."""
    def __init__(
        self,
        server_address: Tuple[str, int],
        RequestHandlerClass: type,
        gateway: OpenDotsMCPGateway,
        max_concurrency: int = 8,
    ) -> None:
        self.gateway = gateway
        self.concurrency_semaphore = threading.Semaphore(max_concurrency)
        super().__init__(server_address, RequestHandlerClass)


# ---------------------------------------------------------------------------
# Gateway Service Controller
# ---------------------------------------------------------------------------

class OpenDotsGatewayService:
    """Manages the lifecycle of the OpenDots HTTP Server thread."""

    def __init__(self, gateway: OpenDotsMCPGateway) -> None:
        self.gateway = gateway
        self._server: Optional[OpenDotsHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    @property
    def bound_port(self) -> int:
        with self._lock:
            if self._server:
                return self._server.server_address[1]
            return self.gateway.config.port

    def start(self) -> None:
        """Start the loopback HTTP server if enabled in config."""
        with self._lock:
            if self._is_running:
                return

            # Security validation
            self.gateway.config.validate()

            if not self.gateway.config.enabled:
                logger.info("OpenDots gateway is disabled by configuration. Server will not start.")
                return

            server_addr = (self.gateway.config.host, self.gateway.config.port)
            self._server = OpenDotsHTTPServer(
                server_addr,
                OpenDotsHTTPRequestHandler,
                self.gateway,
                max_concurrency=self.gateway.config.max_concurrency,
            )

            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="OpenDotsGatewayServer",
                daemon=True,
            )
            self._is_running = True
            self._thread.start()
            logger.info(
                "OpenDots Interoperability Gateway listening on http://%s:%d",
                self.gateway.config.host,
                self.bound_port,
            )

    def stop(self) -> None:
        """Cleanly terminate the server and close the listening socket."""
        with self._lock:
            if not self._is_running or not self._server:
                return

            logger.info("Stopping OpenDots Gateway...")
            self._server.shutdown()
            self._server.server_close()
            self._is_running = False
            self._server = None
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            self._thread = None
            logger.info("OpenDots Gateway stopped successfully.")
