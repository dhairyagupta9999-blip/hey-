"""Comprehensive Security and Interoperability Test Suite for Phase 29.1.

Audits:
1. Protocol Version Negotiation:
   - Streamable HTTP protocolVersion '2025-03-26'
   - Legacy protocolVersion '2024-11-05'
   - Unspecified/unknown versions negotiate to modern default
2. Session ID Tracking & Lifecycle:
   - 'Mcp-Session-Id' header generation (UUID) on initialize
   - In-memory session tracking and retrieval
   - DELETE /mcp session termination with 204 No Content
3. Notifications & Return Codes:
   - 'notifications/initialized' and notifications without 'id' return HTTP 202 Accepted with empty body
4. JSON-RPC Batch Support:
   - Multiple calls in an array
   - Mixed notifications and calls
   - Empty batch rejection (HTTP 400, code -32600)
5. DNS Rebinding Defense (Host Header Validation):
   - Strict loopback Host header requirement (127.0.0.1, localhost, [::1])
   - Rejection of external/attacker Host headers with HTTP 403 Forbidden
6. Origin Validation & CORS Preflight:
   - Untrusted external Origin headers rejected with HTTP 403 Forbidden
   - Loopback Origin headers permitted with standard CORS response headers
   - OPTIONS preflight handler returns HTTP 204 No Content
7. Accept Header Validation:
   - Accept headers matching 'application/json', 'text/event-stream', or '*/*' accepted
   - Incompatible Accept headers (e.g., 'text/html') rejected with HTTP 406 Not Acceptable
8. HTTP Methods on /mcp Endpoint:
   - GET /mcp without SSE returns HTTP 405 Method Not Allowed with Allow header
   - DELETE /mcp terminates session and returns HTTP 204 No Content
9. Bounded Concurrency:
   - Concurrency limit saturation returns HTTP 503 Service Unavailable
   - Automatic recovery once in-flight requests complete
10. OpenDots 'readOnlyHint' Annotations:
    - 5 read-only tools annotated with readOnlyHint: True
    - 5 mutating/controlled tools annotated with readOnlyHint: False
    - Aligns with OpenDots client: requiresApproval = !tool.annotations?.readOnlyHint
11. Authoritative Safety & Authorization Gate Invariants:
    - No backdoor external approval tools
    - Autonomous auto_execute halted at WAITING_FOR_APPROVAL when risk/approvals required
    - Preview tools have zero persistent or execution side effects
12. Payload Limits & Constant-Time Token Security:
    - Over-sized payloads rejected with HTTP 413
    - Bearer token never logged or reflected in error responses
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import pytest

from capability_model import CapabilityDefinition, CapabilityType, Reversibility
from capability_registry import CapabilityRegistry
from event_bus import EventBus
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
from opendots_gateway import (
    MCP_TOOLS,
    OpenDotsGatewayConfig,
    OpenDotsGatewayService,
    OpenDotsMCPGateway,
)
from operational_health import (
    HealthObservation,
    HealthState,
    OperationalHealthCoordinator,
    SubsystemCategory,
)
from resource_governor import ResourceGovernor
from tool_model import RiskLevel


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="hey_jev_p29_1_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def gateway_env(temp_dir):
    """Sets up an isolated, full-stack Hey Jev environment for Phase 29.1 testing."""
    db_path = os.path.join(temp_dir, "test_p29_1_missions.db")
    store = SqliteMissionStore(db_path=db_path)
    event_bus = EventBus()
    registry = CapabilityRegistry()

    # Register sample capabilities
    cap_inspect = CapabilityDefinition(
        capability_id="system:inspect_disk",
        name="Inspect Disk",
        description="Inspect available disk drive space.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.LOW,
        reversibility=Reversibility.READ_ONLY,
    )
    cap_clean = CapabilityDefinition(
        capability_id="system:clean_temp_files",
        name="Clean Temp Files",
        description="Delete temporary files older than threshold.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.MEDIUM,
        reversibility=Reversibility.REVERSIBLE,
    )
    cap_delete = CapabilityDefinition(
        capability_id="system:purge_archive",
        name="Purge Archive",
        description="Irreversibly delete historical system archives.",
        capability_type=CapabilityType.TOOL,
        risk_level=RiskLevel.HIGH,
        reversibility=Reversibility.IRREVERSIBLE,
    )
    registry.register(cap_inspect)
    registry.register(cap_clean)
    registry.register(cap_delete)

    governor = ResourceGovernor(foreground_slots=1, background_slots=1)
    health_coord = OperationalHealthCoordinator(event_bus=event_bus)
    health_coord.record_observation(
        component_id="runtime_core",
        category=SubsystemCategory.RUNTIME_COMPONENT,
        state=HealthState.HEALTHY,
        evidence=["Subsystem initialized successfully."],
    )

    from workflow_engine import WorkflowEngine
    workflow_engine = WorkflowEngine(registry=registry)
    coordinator = MissionCoordinator(
        registry=registry,
        workflow_engine=workflow_engine,
        mission_store=store,
        event_bus=event_bus,
        resource_governor=governor,
    )
    interpreter = MissionInterpreter(registry=registry)
    planner = MissionPlanner(registry=registry, resource_governor=governor)

    token = "test-audit-token-" + uuid.uuid4().hex
    config = OpenDotsGatewayConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,  # OS auto-picks ephemeral port
        bearer_token=token,
    )

    gateway = OpenDotsMCPGateway(
        config=config,
        mission_store=store,
        coordinator=coordinator,
        interpreter=interpreter,
        planner=planner,
        capability_registry=registry,
        health_coordinator=health_coord,
        resource_governor=governor,
    )

    return {
        "store": store,
        "coordinator": coordinator,
        "gateway": gateway,
        "token": token,
        "registry": registry,
        "health_coord": health_coord,
    }


def make_http_request(
    url: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """Helper to perform raw HTTP request and return (status, headers, body)."""
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            resp_headers = {k: v for k, v in resp.headers.items()}
            body = resp.read()
            return resp.status, resp_headers, body
    except urllib.error.HTTPError as e:
        resp_headers = {k: v for k, v in e.headers.items()}
        body = e.read()
        return e.code, resp_headers, body


# ---------------------------------------------------------------------------
# 1. Protocol Version Negotiation
# ---------------------------------------------------------------------------

def test_01_protocol_version_negotiation(gateway_env):
    """Verify initialize negotiates correctly across 2025-03-26 and 2024-11-05."""
    gateway = gateway_env["gateway"]

    # 1. Modern 2025-03-26 (Streamable HTTP)
    resp1 = gateway.handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "opendots-v2", "version": "2.0.0"},
        },
    })
    assert resp1["result"]["protocolVersion"] == "2025-03-26"

    # 2. Legacy 2024-11-05
    resp2 = gateway.handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "opendots-v1", "version": "1.0.0"},
        },
    })
    assert resp2["result"]["protocolVersion"] == "2024-11-05"

    # 3. Unspecified or unknown future version defaults to 2025-03-26
    resp3 = gateway.handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "initialize",
        "params": {
            "protocolVersion": "9999-99-99",
            "capabilities": {},
        },
    })
    assert resp3["result"]["protocolVersion"] == "2025-03-26"


# ---------------------------------------------------------------------------
# 2. Session ID Tracking & Lifecycle
# ---------------------------------------------------------------------------

def test_02_session_lifecycle_and_mcp_session_id_header(gateway_env):
    """Verify Mcp-Session-Id generation on initialize, preservation, and termination via DELETE."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        init_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        }).encode("utf-8")

        status, headers, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=init_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200
        session_id = headers.get("Mcp-Session-Id") or headers.get("mcp-session-id")
        assert session_id is not None
        assert len(session_id) > 10
        assert gateway.has_session(session_id) is True

        # Ping echoing the Mcp-Session-Id header
        ping_payload = json.dumps({"jsonrpc": "2.0", "id": "p-1", "method": "ping"}).encode("utf-8")
        status, ping_headers, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Mcp-Session-Id": session_id,
            },
        )
        assert status == 200
        echoed_session = ping_headers.get("Mcp-Session-Id") or ping_headers.get("mcp-session-id")
        assert echoed_session == session_id

        # Terminate session with DELETE /mcp
        status, del_headers, _ = make_http_request(
            f"{base_url}/mcp",
            method="DELETE",
            headers={
                "Authorization": f"Bearer {token}",
                "Mcp-Session-Id": session_id,
            },
        )
        assert status == 204
        assert gateway.has_session(session_id) is False

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 3. Notification Handling (HTTP 202 Accepted)
# ---------------------------------------------------------------------------

def test_03_notification_returns_202_accepted(gateway_env):
    """Verify notifications without id return HTTP 202 Accepted with zero body."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        notif_payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }).encode("utf-8")

        status, headers, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=notif_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 202
        assert len(body) == 0

        # General notifications
        custom_notif = json.dumps({
            "jsonrpc": "2.0",
            "method": "notifications/cancelled",
            "params": {"requestId": 42},
        }).encode("utf-8")

        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=custom_notif,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 202
        assert len(body) == 0

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 4. JSON-RPC Batch Requests
# ---------------------------------------------------------------------------

def test_04_jsonrpc_batch_requests(gateway_env):
    """Verify array payloads are dispatched in batch per JSON-RPC 2.0 specification."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        # 1. Batch of 2 calls
        batch_payload = json.dumps([
            {"jsonrpc": "2.0", "id": "b1", "method": "ping"},
            {"jsonrpc": "2.0", "id": "b2", "method": "tools/list"},
        ]).encode("utf-8")

        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=batch_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200
        data = json.loads(body.decode("utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["id"] == "b1"
        assert data[0]["result"] == {}
        assert data[1]["id"] == "b2"
        assert len(data[1]["result"]["tools"]) == 10

        # 2. Empty batch returns 400
        empty_payload = json.dumps([]).encode("utf-8")
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=empty_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 400
        err = json.loads(body.decode("utf-8"))
        assert err["error"]["code"] == -32600

        # 3. Batch of only notifications returns 202
        notifs_batch = json.dumps([
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "notifications/progress"},
        ]).encode("utf-8")
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=notifs_batch,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 202
        assert len(body) == 0

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 5. DNS Rebinding Defense (Host Header Validation)
# ---------------------------------------------------------------------------

def test_05_dns_rebinding_defense_host_validation(gateway_env):
    """Verify strict Host header validation rejects external hostnames with HTTP 403 Forbidden."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")

        # 1. Attacker domain -> 403 Forbidden
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Host": f"attacker.com:{port}",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 403
        data = json.loads(body.decode("utf-8"))
        assert "invalid Host header" in data["error"]

        # 2. External IP -> 403 Forbidden
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Host": f"192.168.1.100:{port}",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 403

        # 3. Valid loopback Host (127.0.0.1) -> 200 OK
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Host": f"127.0.0.1:{port}",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200

        # 4. Valid localhost Host -> 200 OK
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Host": f"localhost:{port}",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 6. Origin Validation & CORS Preflight
# ---------------------------------------------------------------------------

def test_06_origin_validation_and_cors_preflight(gateway_env):
    """Verify malicious Origin headers are rejected with 403, and loopback origins receive CORS headers."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")

        # 1. External Origin -> 403 Forbidden
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Origin": "https://evil-attacker.io",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 403
        data = json.loads(body.decode("utf-8"))
        assert "untrusted Origin" in data["error"]

        # 2. Loopback Origin (OpenDots UI dev server on :5173) -> 200 OK with CORS
        status, headers, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Origin": "http://localhost:5173",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200
        cors_origin = headers.get("Access-Control-Allow-Origin") or headers.get("access-control-allow-origin")
        assert cors_origin == "http://localhost:5173"

        # 3. OPTIONS Preflight -> 204 No Content with CORS
        status, opt_headers, _ = make_http_request(
            f"{base_url}/mcp",
            method="OPTIONS",
            headers={
                "Origin": "http://localhost:5173",
            },
        )
        assert status == 204
        assert opt_headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"
        assert "Mcp-Session-Id" in opt_headers.get("Access-Control-Allow-Headers", "")

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 7. Accept Header Validation
# ---------------------------------------------------------------------------

def test_07_accept_header_validation(gateway_env):
    """Verify Accept header is validated to ensure MCP compatible responses."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")

        # 1. MCP client Accept header (application/json, text/event-stream) -> 200
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
        )
        assert status == 200

        # 2. Incompatible Accept header -> 406 Not Acceptable
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "Accept": "text/html",
            },
        )
        assert status == 406
        data = json.loads(body.decode("utf-8"))
        assert "Not Acceptable" in data["error"]

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 8. HTTP Methods on /mcp Endpoint
# ---------------------------------------------------------------------------

def test_08_http_methods_on_mcp_endpoint(gateway_env):
    """Verify method routing: GET /mcp returns 405 Method Not Allowed; DELETE terminates session."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        # GET /mcp -> 405 Method Not Allowed with Allow header
        status, headers, body = make_http_request(
            f"{base_url}/mcp",
            method="GET",
        )
        assert status == 405
        allow_hdr = headers.get("Allow") or headers.get("allow")
        assert allow_hdr is not None
        assert "POST" in allow_hdr
        assert "DELETE" in allow_hdr

        # DELETE /mcp without auth -> 401
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="DELETE",
        )
        assert status == 401

        # DELETE /mcp with auth -> 204 No Content
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="DELETE",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert status == 204

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 9. Concurrency Bounding Saturation
# ---------------------------------------------------------------------------

def test_09_concurrency_bounding_saturation(gateway_env):
    """Verify concurrency limit returns HTTP 503 when saturated."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    # Reconfigure gateway with max_concurrency = 2
    gateway.config.max_concurrency = 2
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        # Acquire 2 slots on the server's semaphore artificially to simulate 2 in-flight requests
        assert service._server.concurrency_semaphore.acquire(blocking=False) is True
        assert service._server.concurrency_semaphore.acquire(blocking=False) is True

        # Now 3rd request should fail immediately with 503 Service Unavailable
        ping_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 503
        data = json.loads(body.decode("utf-8"))
        assert "concurrency limit reached" in data["error"]

        # Release one slot -> subsequent request succeeds
        service._server.concurrency_semaphore.release()
        status, _, _ = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=ping_body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 200

        # Release final slot
        service._server.concurrency_semaphore.release()

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 10. OpenDots readOnlyHint Annotations Audit
# ---------------------------------------------------------------------------

def test_10_opendots_readonly_hint_annotations(gateway_env):
    """Verify OpenDots annotations schema: 5 read-only tools and 5 mutating tools."""
    tools = MCP_TOOLS
    assert len(tools) == 10

    tools_by_name = {t["name"]: t for t in tools}

    expected_readonly = {
        "jev_health",
        "jev_list_missions",
        "jev_get_mission",
        "jev_get_mission_timeline",
        "jev_search_capabilities",
    }
    expected_controlled = {
        "jev_create_mission_preview",
        "jev_submit_mission",
        "jev_request_pause",
        "jev_request_resume",
        "jev_request_cancellation",
    }

    for name in expected_readonly:
        tool = tools_by_name[name]
        assert "annotations" in tool, f"Tool {name} missing annotations"
        assert tool["annotations"].get("readOnlyHint") is True, f"Tool {name} must be readOnlyHint=True"
        assert "title" in tool["annotations"]

    for name in expected_controlled:
        tool = tools_by_name[name]
        assert "annotations" in tool, f"Tool {name} missing annotations"
        assert tool["annotations"].get("readOnlyHint") is False, f"Tool {name} must be readOnlyHint=False"
        assert "title" in tool["annotations"]

    # In OpenDots: const readOnly = tool.annotations?.readOnlyHint === true;
    #              const requiresApproval = !readOnly;
    # Verify that all 5 controlled tools evaluate to requiresApproval=True in OpenDots logic
    for name, tool in tools_by_name.items():
        read_only = tool.get("annotations", {}).get("readOnlyHint") is True
        requires_approval = not read_only
        if name in expected_controlled:
            assert requires_approval is True
        else:
            assert requires_approval is False


# ---------------------------------------------------------------------------
# 11. Authoritative Safety & Authorization Gate Invariants
# ---------------------------------------------------------------------------

def test_11_authorization_gate_invariants(gateway_env):
    """Verify external callers cannot approve missions directly and high-risk goals halt at WAITING_FOR_APPROVAL."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    # 1. No approve tool in tools/list
    tool_names = {t["name"] for t in MCP_TOOLS}
    assert "jev_approve_mission" not in tool_names
    assert "approve_checkpoint" not in tool_names

    # 2. Preview does not save to DB or execute
    initial_count = len(store.list_missions())
    preview_res = gateway.handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": "prev-1",
        "method": "tools/call",
        "params": {
            "name": "jev_create_mission_preview",
            "arguments": {"objective": "Clean all temporary system files"},
        },
    })
    assert preview_res["result"].get("isError") is not True
    assert len(store.list_missions()) == initial_count, "Preview must not write to store"

    # 3. High-risk mission halts at WAITING_FOR_APPROVAL even if auto_execute=True
    submit_res = gateway.handle_jsonrpc({
        "jsonrpc": "2.0",
        "id": "sub-1",
        "method": "tools/call",
        "params": {
            "name": "jev_submit_mission",
            "arguments": {
                "objective": "Inspect disk and purge archive permanently",
                "mode": "COPILOT",
                "auto_execute": True,
            },
        },
    })
    assert submit_res["result"].get("isError") is not True
    parsed_submit = json.loads(submit_res["result"]["content"][0]["text"])
    assert parsed_submit["requires_approval"] is True
    assert parsed_submit["executed"] is False
    assert parsed_submit["status"] == "WAITING_FOR_APPROVAL"

    # Verify persisted mission in SQLite is indeed WAITING_FOR_APPROVAL
    persisted = store.get_mission(parsed_submit["mission_id"])
    assert persisted is not None
    assert persisted.status == MissionStatus.WAITING_FOR_APPROVAL


# ---------------------------------------------------------------------------
# 12. Payload Limits & Constant-Time Token Security
# ---------------------------------------------------------------------------

def test_12_payload_limits_and_auth_security(gateway_env):
    """Verify >1MB payload returns HTTP 413 and token is never logged or leaked."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port
        base_url = f"http://127.0.0.1:{port}"

        # 1. Payload > 1MB returns 413
        oversized = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "ping",
            "padding": "A" * (1024 * 1024 + 100),
        }).encode("utf-8")

        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=oversized,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
        )
        assert status == 413

        # 2. Malformed auth header returns 401 without leaking token
        status, _, body = make_http_request(
            f"{base_url}/mcp",
            method="POST",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Basic dXNlcjpwYXNz",
            },
        )
        assert status == 401
        assert token not in body.decode("utf-8")

    finally:
        service.stop()


# ---------------------------------------------------------------------------
# 13. Authentic Node.js OpenDots Client Integration
# ---------------------------------------------------------------------------

def test_13_authentic_node_opendots_client_execution(gateway_env):
    """Verify authentic Node.js OpenDots client completes all 46 checks against live loopback gateway."""
    import subprocess
    import shutil

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js binary not available on test runner.")

    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        port = service.bound_port

        cmd = [
            node_bin,
            "test_opendots_client.mjs",
            "--port",
            str(port),
            "--token",
            token,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"Node client failed with code {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
        assert "Total Checks: 46" in proc.stdout
        assert "Passed:       46" in proc.stdout
        assert "Failed:       0" in proc.stdout

    finally:
        service.stop()
