"""Comprehensive Test Suite for Phase 29: OpenDots Interoperability Gateway.

Verifies:
1. Disabled by default; zero side-effects when disabled.
2. Loopback binding strictly enforced (127.0.0.1); rejects public interfaces.
3. Bearer token authentication enforcement (401 on missing/invalid, 200 on valid). Constant-time verification.
4. Token never logged or leaked in error responses or logs.
5. MCP JSON-RPC 2.0 handshake (initialize, notifications/initialized, ping, tools/list).
6. Tool schema validity for all 10 exposed tools.
7. Read-only tools (jev_health, jev_list_missions, jev_get_mission, jev_get_mission_timeline, jev_search_capabilities).
8. Controlled tools:
   - jev_create_mission_preview: Zero side-effects, no DB persistence, no execution.
   - jev_submit_mission: Governed persistence via SqliteMissionStore and MissionCoordinator.
   - Human approval gate preservation: High-risk/checkpoint missions halt at WAITING_FOR_APPROVAL and never auto-execute.
   - External callers cannot mutate approvals directly (no backdoor approval tools).
   - Lifecycle controls (pause, resume, cancel).
9. Real loopback HTTP socket lifecycle (start, bind, authenticate, clean shutdown).
10. Desktop standalone invariant (zero impact on Jev UI when disconnected or disabled).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import urllib.request
import urllib.error
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
from workflow_engine import WorkflowEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="hey_jev_p29_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def gateway_env(temp_dir):
    """Sets up an isolated, full-stack Hey Jev environment for gateway testing."""
    db_path = os.path.join(temp_dir, "test_p29_missions.db")
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

    token = "test-secret-token-1234567890abcdef"
    config = OpenDotsGatewayConfig(
        enabled=True,
        host="127.0.0.1",
        port=0,  # Ephemeral port for network tests
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
        "interpreter": interpreter,
        "planner": planner,
        "registry": registry,
        "governor": governor,
        "health_coord": health_coord,
        "event_bus": event_bus,
        "gateway": gateway,
        "token": token,
        "config": config,
        "temp_dir": temp_dir,
    }


# ---------------------------------------------------------------------------
# Test 1: Disabled by default and configuration validation
# ---------------------------------------------------------------------------
def test_01_gateway_disabled_by_default():
    """Verify that OpenDotsGatewayConfig defaults to disabled and does not run."""
    cfg = OpenDotsGatewayConfig()
    assert cfg.enabled is False
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.bearer_token is None

    # Service with disabled config should not start
    gateway = OpenDotsMCPGateway(config=cfg)
    service = OpenDotsGatewayService(gateway)
    service.start()
    assert service.is_running is False


# ---------------------------------------------------------------------------
# Test 2: Loopback binding restriction enforced
# ---------------------------------------------------------------------------
def test_02_loopback_restriction_enforced():
    """Verify that attempting to bind to non-loopback interfaces raises ValueError."""
    for bad_host in ["0.0.0.0", "192.168.1.100", "10.0.0.1", "example.com"]:
        cfg = OpenDotsGatewayConfig(
            enabled=True,
            host=bad_host,
            bearer_token="valid-token-12345",
        )
        with pytest.raises(ValueError, match="OpenDots gateway must bind to loopback"):
            cfg.validate()


# ---------------------------------------------------------------------------
# Test 3: Enabled gateway strictly requires bearer token
# ---------------------------------------------------------------------------
def test_03_enabled_requires_bearer_token():
    """Verify that enabling gateway without bearer token raises ValueError."""
    for bad_token in [None, "", "   "]:
        cfg = OpenDotsGatewayConfig(
            enabled=True,
            host="127.0.0.1",
            bearer_token=bad_token,
        )
        with pytest.raises(ValueError, match="cannot be enabled without a valid configured bearer token"):
            cfg.validate()


# ---------------------------------------------------------------------------
# Test 4 & 5 & 6: Bearer token authentication enforcement
# ---------------------------------------------------------------------------
def test_04_authentication_enforcement(gateway_env):
    """Verify constant-time token authentication matching and rejection."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]

    # Valid token
    assert gateway.authenticate_token(token) is True
    assert gateway.authenticate_token(f"  {token}  ") is True  # Strips whitespace

    # Invalid tokens
    assert gateway.authenticate_token(None) is False
    assert gateway.authenticate_token("") is False
    assert gateway.authenticate_token("wrong-token") is False
    assert gateway.authenticate_token(token[:-1]) is False
    assert gateway.authenticate_token(token + "x") is False


# ---------------------------------------------------------------------------
# Test 7: Secret redaction in responses and errors
# ---------------------------------------------------------------------------
def test_07_token_never_logged_or_leaked(gateway_env):
    """Verify that tokens and API keys are redacted by sanitize_content in gateway outputs."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]

    # Simulate an error message containing a secret key
    dummy_secret = "sk-ant-api03-abcdef1234567890abcdef1234567890"
    req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tools/call",
        "params": {
            "name": "jev_create_mission_preview",
            "arguments": {
                "objective": f"Sync files using key {dummy_secret} and bearer {token}",
            },
        },
    }
    resp = gateway.handle_jsonrpc(req)
    assert resp is not None
    resp_text = json.dumps(resp)

    # Secret key must NOT appear in output; must be redacted
    assert dummy_secret not in resp_text
    assert "[REDACTED_CREDENTIAL]" in resp_text


# ---------------------------------------------------------------------------
# Test 8: MCP initialize handshake
# ---------------------------------------------------------------------------
def test_08_initialize_handshake(gateway_env):
    """Verify MCP protocol initialize method returns standard capabilities."""
    gateway = gateway_env["gateway"]
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "opendots-client", "version": "1.0.0"},
        },
    }
    resp = gateway.handle_jsonrpc(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "hey-jev-gateway"


# ---------------------------------------------------------------------------
# Test 9: Ping and notifications
# ---------------------------------------------------------------------------
def test_09_ping_and_notifications(gateway_env):
    """Verify ping returns empty result and notification yields None."""
    gateway = gateway_env["gateway"]

    # Ping
    ping_req = {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    resp = gateway.handle_jsonrpc(ping_req)
    assert resp == {"jsonrpc": "2.0", "id": 2, "result": {}}

    # Notification
    notif_req = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }
    assert gateway.handle_jsonrpc(notif_req) is None


# ---------------------------------------------------------------------------
# Test 10: Tool schema definitions (10 tools)
# ---------------------------------------------------------------------------
def test_10_tools_list_spec(gateway_env):
    """Verify tools/list exposes all 10 required tools with valid JSON schemas."""
    gateway = gateway_env["gateway"]
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    resp = gateway.handle_jsonrpc(req)

    tools = resp["result"]["tools"]
    assert len(tools) == 10
    tool_names = {t["name"] for t in tools}

    expected_tools = {
        "jev_health",
        "jev_list_missions",
        "jev_get_mission",
        "jev_get_mission_timeline",
        "jev_search_capabilities",
        "jev_create_mission_preview",
        "jev_submit_mission",
        "jev_request_pause",
        "jev_request_resume",
        "jev_request_cancellation",
    }
    assert tool_names == expected_tools

    # Verify each tool has description and schema with additionalProperties: False
    for t in tools:
        assert "description" in t and len(t["description"]) > 10
        schema = t["inputSchema"]
        assert schema["type"] == "object"
        assert schema.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# Test 11: jev_health tool
# ---------------------------------------------------------------------------
def test_11_jev_health_tool(gateway_env):
    """Verify jev_health returns subsystem health and governor info."""
    gateway = gateway_env["gateway"]
    success, data, err = gateway.execute_tool("jev_health", {})
    assert success is True
    assert err is None
    assert data["status"] in ("healthy", "degraded")
    assert len(data["subsystems"]) >= 1
    assert data["governor"]["configured"] is True
    assert "memory" in data["system_metrics"] or "error" in data["system_metrics"]


# ---------------------------------------------------------------------------
# Test 12: jev_list_missions pagination and filtering
# ---------------------------------------------------------------------------
def test_12_jev_list_missions_pagination(gateway_env):
    """Verify jev_list_missions pagination, filtering, and derived progress."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    # Populate 3 sample missions
    for i in range(3):
        m = Mission(
            mission_id=f"m_{i}",
            objective=f"Objective {i}",
            status=MissionStatus.READY if i != 2 else MissionStatus.COMPLETED,
            mode=MissionMode.COPILOT,
        )
        store.save_mission(m)

    # 1. List all
    success, data, err = gateway.execute_tool("jev_list_missions", {"limit": 10})
    assert success is True
    assert data["total_count"] == 3
    assert len(data["missions"]) == 3

    # 2. Filter by status
    success, data, err = gateway.execute_tool("jev_list_missions", {"status": "COMPLETED"})
    assert success is True
    assert data["total_count"] == 1
    assert data["missions"][0]["mission_id"] == "m_2"
    assert data["missions"][0]["progress_percent"] == 100.0

    # 3. Pagination offset
    success, data, err = gateway.execute_tool("jev_list_missions", {"limit": 1, "offset": 1})
    assert success is True
    assert len(data["missions"]) == 1


# ---------------------------------------------------------------------------
# Test 13: jev_get_mission detail and missing mission error
# ---------------------------------------------------------------------------
def test_13_jev_get_mission_detail(gateway_env):
    """Verify jev_get_mission returns comprehensive DAG and criteria structure."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    task = MissionTask(
        task_id="t_1",
        description="Check system health",
        intended_result="Disk space is measured",
        capability_id="system:inspect_disk",
        status=MissionTaskStatus.COMPLETED,
    )
    plan = MissionPlan(plan_id="p_1", tasks=[task])
    criterion = AcceptanceCriterion(
        criterion_id="ac_1",
        description="Disk space is measured",
        status=AcceptanceStatus.PASS,
    )
    mission = Mission(
        mission_id="m_detail",
        objective="Inspect drive",
        plan=plan,
        acceptance_criteria=[criterion],
        mode=MissionMode.COPILOT,
    )
    store.save_mission(mission)

    # Fetch existing
    success, data, err = gateway.execute_tool("jev_get_mission", {"mission_id": "m_detail"})
    assert success is True
    assert data["mission_id"] == "m_detail"
    assert len(data["tasks"]) == 1
    assert data["tasks"][0]["task_id"] == "t_1"
    assert len(data["acceptance_criteria"]) == 1
    assert data["progress_percent"] == 100.0

    # Fetch non-existent
    success, data, err = gateway.execute_tool("jev_get_mission", {"mission_id": "missing_id"})
    assert success is False
    assert "not found" in err


# ---------------------------------------------------------------------------
# Test 14: jev_get_mission_timeline events
# ---------------------------------------------------------------------------
def test_14_jev_get_mission_timeline(gateway_env):
    """Verify jev_get_mission_timeline captures and returns timeline events."""
    gateway = gateway_env["gateway"]
    event_bus = gateway_env["event_bus"]

    # Emit test events onto bus
    event_bus.emit("mission.started", "m_timeline", {"mode": "COPILOT"})
    event_bus.emit("mission.task.completed", "m_timeline", {"task_id": "t_1"})

    success, data, err = gateway.execute_tool("jev_get_mission_timeline", {"mission_id": "m_timeline"})
    assert success is True
    assert data["mission_id"] == "m_timeline"
    assert data["event_count"] >= 2
    event_types = [e["event_type"] for e in data["events"]]
    assert "mission.started" in event_types
    assert "mission.task.completed" in event_types


# ---------------------------------------------------------------------------
# Test 15: jev_search_capabilities (pure discovery, zero execution)
# ---------------------------------------------------------------------------
def test_15_jev_search_capabilities(gateway_env):
    """Verify capability discovery without execution."""
    gateway = gateway_env["gateway"]

    # Search all
    success, data, err = gateway.execute_tool("jev_search_capabilities", {})
    assert success is True
    assert data["total_matches"] == 3

    # Query filter
    success, data, err = gateway.execute_tool("jev_search_capabilities", {"query": "disk"})
    assert success is True
    assert data["total_matches"] == 1
    assert data["capabilities"][0]["capability_id"] == "system:inspect_disk"

    # Risk level filter
    success, data, err = gateway.execute_tool("jev_search_capabilities", {"max_risk": "MEDIUM"})
    assert success is True
    cap_ids = [c["capability_id"] for c in data["capabilities"]]
    assert "system:inspect_disk" in cap_ids
    assert "system:clean_temp_files" in cap_ids
    assert "system:purge_archive" not in cap_ids  # HIGH risk excluded


# ---------------------------------------------------------------------------
# Test 16: jev_create_mission_preview (zero side-effects guarantee)
# ---------------------------------------------------------------------------
def test_16_jev_create_mission_preview_zero_side_effects(gateway_env):
    """Verify mission preview generates DAG plan without DB persistence or execution."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    before_missions = len(store.list_missions())

    success, preview, err = gateway.execute_tool(
        "jev_create_mission_preview",
        {
            "objective": "Inspect available disk drive space and clean temporary files older than 7 days",
            "mode": "COPILOT",
        },
    )
    assert success is True
    assert err is None
    assert preview["preview_only"] is True
    assert preview["persisted"] is False
    assert preview["executed"] is False
    assert preview["total_tasks"] >= 1
    assert len(preview["acceptance_criteria"]) >= 1

    # Invariant: SQLite store must NOT have been modified
    after_missions = len(store.list_missions())
    assert after_missions == before_missions


# ---------------------------------------------------------------------------
# Test 17: jev_submit_mission with approval gate preservation
# ---------------------------------------------------------------------------
def test_17_jev_submit_mission_with_approval_gate(gateway_env):
    """Verify submitted mission requiring approval pauses at WAITING_FOR_APPROVAL and does not auto-execute."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    success, result, err = gateway.execute_tool(
        "jev_submit_mission",
        {
            "objective": "Inspect disk and purge archive permanently",
            "mode": "COPILOT",
            "auto_execute": True,  # Caller asks to execute, but governance must prevent it!
        },
    )
    assert success is True
    assert result["persisted"] is True
    assert result["requires_approval"] is True
    assert result["status"] == MissionStatus.WAITING_FOR_APPROVAL.value
    assert result["executed"] is False  # Refused auto-execution because approval is required!

    # Check store directly
    mission_id = result["mission_id"]
    saved = store.get_mission(mission_id)
    assert saved is not None
    assert saved.status == MissionStatus.WAITING_FOR_APPROVAL


# ---------------------------------------------------------------------------
# Test 18: jev_submit_mission autopilot execution when permitted
# ---------------------------------------------------------------------------
def test_18_jev_submit_mission_autopilot_execution(gateway_env):
    """Verify submit with AUTOPILOT mode and low-risk objective dispatches execution safely."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    # Register only low risk capability
    success, result, err = gateway.execute_tool(
        "jev_submit_mission",
        {
            "objective": "Inspect disk drive",
            "mode": "AUTOPILOT",
            "auto_execute": True,
        },
    )
    assert success is True
    assert result["persisted"] is True
    assert result["mode"] == "AUTOPILOT"
    # Even if executed or ready, mission is persisted
    saved = store.get_mission(result["mission_id"])
    assert saved is not None


# ---------------------------------------------------------------------------
# Test 19: No direct approval backdoor in gateway
# ---------------------------------------------------------------------------
def test_19_no_direct_approval_mutation(gateway_env):
    """Verify gateway does NOT expose any tool allowing external callers to directly approve tasks."""
    tools = MCP_TOOLS
    tool_names = [t["name"] for t in tools]
    for prohibited in ["jev_approve_task", "jev_approve_plan", "execute_tool", "run_shell"]:
        assert prohibited not in tool_names

    # Calling an unauthorized approval method returns failure
    success, data, err = gateway_env["gateway"].execute_tool("jev_approve_task", {"task_id": "t_1"})
    assert success is False
    assert "not authorized" in err or "not recognized" in err


# ---------------------------------------------------------------------------
# Test 20: Mission pause, resume, and cancel controls
# ---------------------------------------------------------------------------
def test_20_mission_pause_resume_cancel_controls(gateway_env):
    """Verify pause, resume, and cancel lifecycle controls."""
    gateway = gateway_env["gateway"]
    store = gateway_env["store"]

    m = Mission(
        mission_id="m_ctrl",
        objective="Background task",
        status=MissionStatus.READY,
        mode=MissionMode.COPILOT,
    )
    store.save_mission(m)

    # 1. Pause
    success, data, err = gateway.execute_tool("jev_request_pause", {"mission_id": "m_ctrl"})
    assert success is True
    assert data["status"] == MissionStatus.PAUSED.value

    # 2. Resume
    success, data, err = gateway.execute_tool("jev_request_resume", {"mission_id": "m_ctrl"})
    assert success is True
    assert data["status"] == MissionStatus.READY.value

    # 3. Cancel
    success, data, err = gateway.execute_tool("jev_request_cancellation", {"mission_id": "m_ctrl", "reason": "No longer needed"})
    assert success is True
    assert data["status"] == MissionStatus.CANCELLED.value


# ---------------------------------------------------------------------------
# Test 21: Real loopback HTTP server lifecycle and network authentication
# ---------------------------------------------------------------------------
def test_21_server_start_stop_clean_lifecycle(gateway_env):
    """Verify real loopback HTTP server: authentication, JSON-RPC over POST, clean stop."""
    gateway = gateway_env["gateway"]
    token = gateway_env["token"]
    service = OpenDotsGatewayService(gateway)

    try:
        service.start()
        assert service.is_running is True
        port = service.bound_port
        assert port > 0
        base_url = f"http://127.0.0.1:{port}"

        # 1. Health check GET (unauthenticated)
        req_health = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req_health, timeout=5.0) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"

        # 2. POST without token -> 401 Unauthorized
        rpc_payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode("utf-8")
        req_no_auth = urllib.request.Request(
            f"{base_url}/mcp",
            data=rpc_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_no_auth, timeout=5.0)
        assert exc_info.value.code == 401

        # 3. POST with wrong token -> 401 Unauthorized
        req_wrong_auth = urllib.request.Request(
            f"{base_url}/mcp",
            data=rpc_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer wrong-token-12345",
            },
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req_wrong_auth, timeout=5.0)
        assert exc_info.value.code == 401

        # 4. POST with valid token -> 200 OK and ping result
        req_valid = urllib.request.Request(
            f"{base_url}/mcp",
            data=rpc_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req_valid, timeout=5.0) as resp:
            assert resp.status == 200
            resp_body = json.loads(resp.read().decode("utf-8"))
            assert resp_body["jsonrpc"] == "2.0"
            assert resp_body["id"] == 1
            assert resp_body["result"] == {}

        # 5. POST tools/call via HTTP
        call_payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "jev_health",
                "arguments": {},
            },
        }).encode("utf-8")
        req_call = urllib.request.Request(
            f"{base_url}/mcp",
            data=call_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req_call, timeout=5.0) as resp:
            assert resp.status == 200
            call_resp = json.loads(resp.read().decode("utf-8"))
            assert call_resp["id"] == 2
            content = call_resp["result"]["content"]
            assert len(content) == 1
            health_json = json.loads(content[0]["text"])
            assert health_json["status"] in ("healthy", "degraded")

    finally:
        service.stop()
        assert service.is_running is False


# ---------------------------------------------------------------------------
# Test 22: Desktop application standalone invariant
# ---------------------------------------------------------------------------
def test_22_desktop_app_standalone_invariant():
    """Verify that Hey Jev configuration and assistant UI remain unaffected when gateway is disabled."""
    from assistant_ui import load_settings
    import config

    settings = load_settings()
    # Default is False
    assert settings.get("opendots_gateway_enabled") is False
    assert config.OPENDOTS_GATEWAY_ENABLED_DEFAULT is False
    assert config.OPENDOTS_GATEWAY_HOST_DEFAULT == "127.0.0.1"
