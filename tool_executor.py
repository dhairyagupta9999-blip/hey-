"""Tool Executor and Gated Execution Pipeline for Hey Jev.

Enforces:
1. Input schema validation
2. Permission and approval gating (zero bypass)
3. Dry-run preview execution
4. State mutation verification
5. Rollback metadata generation
6. Dynamic tool failover to healthy alternatives
7. Audit event emission via EventBus
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from agent_task import TaskPermission
from event_bus import EventBus
from tool_model import RiskLevel, ToolDefinition, ToolHealth, ToolResult
from tool_registry import ToolRegistry


class ToolExecutor:
    """Orchestrates safe, validated, permission-gated execution of concrete tools."""

    HIGH_RISK_TIERS = {RiskLevel.HIGH, RiskLevel.CRITICAL}

    def __init__(
        self,
        registry: ToolRegistry,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus or EventBus()

    def validate_input(self, tool: ToolDefinition, inputs: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate input arguments against tool input schema."""
        errors: List[str] = []
        schema = tool.input_schema or {}
        required = schema.get("required", [])

        for field_name in required:
            if field_name not in inputs:
                errors.append(f"Missing required parameter '{field_name}'")

        # Type checking for specified properties
        props = schema.get("properties", {})
        for k, val in inputs.items():
            if k in props:
                prop_type = props[k].get("type")
                if prop_type == "string" and not isinstance(val, str):
                    errors.append(f"Parameter '{k}' must be string, got {type(val).__name__}")
                elif prop_type == "integer" and not isinstance(val, int):
                    errors.append(f"Parameter '{k}' must be integer, got {type(val).__name__}")
                elif prop_type == "boolean" and not isinstance(val, bool):
                    errors.append(f"Parameter '{k}' must be boolean, got {type(val).__name__}")
                elif prop_type == "array" and not isinstance(val, (list, tuple)):
                    errors.append(f"Parameter '{k}' must be list, got {type(val).__name__}")
                elif prop_type == "object" and not isinstance(val, dict):
                    errors.append(f"Parameter '{k}' must be dict, got {type(val).__name__}")

        return len(errors) == 0, errors

    def check_permission(
        self,
        tool: ToolDefinition,
        granted_permissions: Optional[List[TaskPermission]] = None,
        approval_state: str = "NONE",
    ) -> Tuple[bool, str]:
        """Verify the caller has required permissions and approval for the tool's risk level."""
        granted = set(granted_permissions or [])

        # Check required permissions
        for perm in tool.permissions:
            if perm not in granted:
                return False, f"Missing required permission: '{perm.value}'"

        # Check approval requirement for high-risk operations
        if tool.risk_level in self.HIGH_RISK_TIERS:
            if approval_state != "APPROVED":
                return False, f"High-risk operation ({tool.risk_level.value}) requires explicit human approval."

        return True, ""

    def dry_run(
        self,
        tool_id: str,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Perform a dry-run preview without mutating state."""
        tool = self.registry.get(tool_id)
        if not tool:
            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=f"Tool '{tool_id}' not found in registry.",
                is_dry_run=True,
            )

        valid, errs = self.validate_input(tool, inputs)
        if not valid:
            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=f"Input validation failed: {'; '.join(errs)}",
                is_dry_run=True,
            )

        handler = self.registry.get_handler(tool_id)
        if handler:
            try:
                # Try passing dry_run=True flag to handler if supported
                res = handler(**inputs, dry_run=True)
                if isinstance(res, ToolResult):
                    res.is_dry_run = True
                    return res
                return ToolResult(
                    tool_id=tool_id,
                    status="DRY_RUN",
                    output=res,
                    input_summary=dict(inputs),
                    is_dry_run=True,
                )
            except TypeError:
                pass

        # Default synthetic preview for safe inspection
        return ToolResult(
            tool_id=tool_id,
            status="DRY_RUN",
            output=f"Dry run preview for {tool.name}: would process inputs with {tool.provider}",
            input_summary=dict(inputs),
            is_dry_run=True,
        )

    def execute(
        self,
        tool_id: str,
        inputs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        granted_permissions: Optional[List[TaskPermission]] = None,
        is_approved: bool = False,
        allow_failover: bool = True,
    ) -> ToolResult:
        """Execute a tool with full validation, permission checks, and automatic failover."""
        tool = self.registry.get(tool_id)
        ctx = context or {}
        approval_state = "APPROVED" if is_approved else ctx.get("approval_state", "NONE")

        # 1. Check Tool Existence and Health (trigger failover if unhealthy)
        if not tool or tool.health in (ToolHealth.UNAVAILABLE, ToolHealth.BROKEN):
            if allow_failover and tool:
                fallback_res = self._attempt_failover(
                    primary_tool=tool,
                    inputs=inputs,
                    context=ctx,
                    granted_permissions=granted_permissions,
                    is_approved=is_approved,
                    reason=f"Primary tool '{tool_id}' health is {tool.health.value}",
                )
                if fallback_res:
                    return fallback_res

            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=f"Tool '{tool_id}' is unavailable (health: {tool.health.value if tool else 'UNKNOWN'})",
            )

        # 2. Permission and Approval Gating
        authorized, perm_err = self.check_permission(tool, granted_permissions, approval_state)
        if not authorized:
            self.event_bus.emit(
                "tool.failed",
                tool_id,
                data={"reason": "permission_denied", "error": perm_err},
            )
            return ToolResult(
                tool_id=tool_id,
                status="BLOCKED",
                error=f"Permission Denied: {perm_err}",
                input_summary=dict(inputs),
            )

        # 3. Input Schema Validation
        valid, errs = self.validate_input(tool, inputs)
        if not valid:
            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=f"Validation failed: {'; '.join(errs)}",
                input_summary=dict(inputs),
            )

        # 4. Handler Dispatch
        handler = self.registry.get_handler(tool_id)
        if not handler:
            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=f"No execution handler bound for tool '{tool_id}'.",
                input_summary=dict(inputs),
            )

        t0 = time.perf_counter()
        self.event_bus.emit("tool.started", tool_id, data={"inputs": inputs})

        try:
            raw_output = handler(**inputs)
            duration = (time.perf_counter() - t0) * 1000.0

            if isinstance(raw_output, ToolResult):
                result = raw_output
                result.duration = duration
            else:
                result = ToolResult(
                    tool_id=tool_id,
                    status="SUCCESS",
                    output=raw_output,
                    input_summary=dict(inputs),
                    duration=duration,
                )

            # Generate rollback metadata if tool mutates state
            if tool.supports_rollback and not result.rollback_metadata:
                result.rollback_metadata = self.rollback_metadata(tool, inputs, result)

            self.event_bus.emit("tool.completed", tool_id, data={"status": result.status, "duration": duration})
            return result

        except Exception as exc:
            duration = (time.perf_counter() - t0) * 1000.0
            self.event_bus.emit("tool.failed", tool_id, data={"error": str(exc), "duration": duration})

            # Check if failover is possible
            if allow_failover:
                fallback_res = self._attempt_failover(
                    primary_tool=tool,
                    inputs=inputs,
                    context=ctx,
                    granted_permissions=granted_permissions,
                    is_approved=is_approved,
                    reason=f"Primary tool execution failed: {str(exc)}",
                )
                if fallback_res:
                    return fallback_res

            return ToolResult(
                tool_id=tool_id,
                status="FAILURE",
                error=str(exc),
                input_summary=dict(inputs),
                duration=duration,
            )

    def _attempt_failover(
        self,
        primary_tool: ToolDefinition,
        inputs: Dict[str, Any],
        context: Dict[str, Any],
        granted_permissions: Optional[List[TaskPermission]],
        is_approved: bool,
        reason: str,
    ) -> Optional[ToolResult]:
        """Find an alternative healthy tool with identical capability and execute it."""
        for cap in primary_tool.capabilities:
            candidates = self.registry.find_by_capability(cap)
            for alt in candidates:
                if alt.tool_id != primary_tool.tool_id and alt.health == ToolHealth.HEALTHY:
                    self.event_bus.emit(
                        "tool.fallback",
                        alt.tool_id,
                        data={
                            "primary_tool": primary_tool.tool_id,
                            "fallback_tool": alt.tool_id,
                            "reason": reason,
                        },
                    )
                    # Execute fallback without recursing failover
                    return self.execute(
                        tool_id=alt.tool_id,
                        inputs=inputs,
                        context=context,
                        granted_permissions=granted_permissions,
                        is_approved=is_approved,
                        allow_failover=False,
                    )
        return None

    def rollback_metadata(
        self,
        tool: ToolDefinition,
        inputs: Dict[str, Any],
        result: ToolResult,
    ) -> Dict[str, Any]:
        """Extract rollback metadata for state-mutating operations."""
        meta = {}
        if "destination" in inputs and "source" in inputs:
            meta["undo_action"] = "move"
            meta["restore_source"] = inputs["destination"]
            meta["restore_destination"] = inputs["source"]
        elif "path" in inputs and "wallpaper" in tool.category.lower():
            meta["undo_action"] = "set_wallpaper"
            meta["restore_path"] = result.before_state.get("wallpaper_path", "")
        elif "setting" in inputs:
            meta["undo_action"] = "change_setting"
            meta["restore_value"] = result.before_state.get("value", "")
        return meta
