# Hey Jev: Phase 29 Implementation Report
## OpenDots Interoperability Gateway

**Date:** October 11, 2026  
**Branch:** `windows-port`  
**Base Commit:** `bd295e4` (Documentation), `af1732e` (Phase 28 Feature)  
**Author:** Antigravity Agent  

---

## 1. Executive Summary

Phase 29 establishes secure, governed interoperability between **Hey Jev** and an external **OpenDots** workspace (CopilotKit AI coworkers platform) using the **Model Context Protocol (MCP)** over JSON-RPC 2.0.

### Core Architectural Principle
**Hey Jev remains the authoritative agent engine. OpenDots is an optional external workspace and interface.**
Hey Jev retains exclusive authority over mission goal interpretation, DAG decomposition, SQLite persistence, task execution, restart recovery, human approval gates, resource governance, and outcome verification. OpenDots connects purely as an external collaborator via an authenticated loopback gateway.

---

## 2. Key Accomplishments

1. **Self-Contained MCP JSON-RPC 2.0 Gateway (`opendots_gateway.py`)**:
   - Zero added external dependencies: built with Python standard library (`http.server`, `socketserver`, `json`, `threading`, `secrets`).
   - Conforms strictly to MCP protocol version `2024-11-05` supporting `initialize`, `notifications/initialized`, `ping`, `tools/list`, and `tools/call`.
   - Binds strictly to loopback (`127.0.0.1`), actively rejecting non-loopback or public bindings.
   - Disabled by default (`enabled=False`). Jev desktop runs completely standalone with zero open ports or listeners unless explicitly enabled.

2. **Mandatory Bearer Token Authentication**:
   - Requires configured bearer token (`JEV_OPENDOTS_BEARER_TOKEN` or secure settings).
   - Validates tokens using constant-time verification (`secrets.compare_digest`) to prevent timing attacks.
   - Unauthenticated, missing, or mismatched tokens immediately return HTTP 401 Unauthorized.
   - Tokens and secrets are never logged, printed, or leaked in errors.

3. **Exposed Toolset (10 Tools)**:
   - **Tier 1 (Read-Only Tools - Autonomous Discovery)**:
     - `jev_health`: Subsystem health observations, memory footprint, CPU load, and resource governor slot occupancy.
     - `jev_list_missions`: Paginated safe summaries (`mission_id`, `objective`, `status`, `mode`, `progress_percent`).
     - `jev_get_mission`: Detailed state, DAG plan, tasks, acceptance criteria, and verification evidence.
     - `jev_get_mission_timeline`: Bounded, deduplicated chronological lifecycle events.
     - `jev_search_capabilities`: Query available tools and skills without execution.
   - **Tier 2 (Controlled Mission Tools - Governed Actions)**:
     - `jev_create_mission_preview`: Normalizes goal, acceptance criteria, and candidate DAG task graph in-memory with **guaranteed zero side-effects** (zero DB persistence, zero execution).
     - `jev_submit_mission`: Submits user-approved missions into Jev's authoritative lifecycle (`SqliteMissionStore`). Enforces human approval policies: missions with checkpoints or risk $\ge$ HIGH halt at `WAITING_FOR_APPROVAL` or `READY` and **never auto-execute**.
     - `jev_request_pause`: Calls `coordinator.pause_mission(mission_id)`.
     - `jev_request_resume`: Calls `coordinator.resume_mission(mission_id)`.
     - `jev_request_cancellation`: Calls `coordinator.cancel_mission(mission_id)`.

4. **Safety Invariants Maintained**:
   - **No Generic Execution**: No `execute_tool` or shell command endpoints exist.
   - **No Approval Bypass**: External callers cannot force approval of tasks or mutate `approved_tasks`. All approvals must go through Jev's native human verification.
   - **Secret Scrubbing**: All responses pass through `memory_model.sanitize_content`.
   - **UI & Invariant Preservation**: Assistant UI retains its 7-tab invariant; standalone desktop mode operates completely unchanged.

5. **Formal Integration Documentation (`docs/integrations/OPENDOTS_INTEGRATION.md`)**:
   - Outlines confirmed OpenDots source patterns, implemented gateway architecture, test-verified capabilities, live-instance constraints on Windows dual-core hardware, and unverified future assumptions.

---

## 3. Test Verification Results

### Dedicated Phase 29 Suite (`test_phase29.py`):
- **20 passed / 20 tests** in 3.72 seconds:
  - `test_01_gateway_disabled_by_default`: PASSED
  - `test_02_loopback_restriction_enforced`: PASSED
  - `test_03_enabled_requires_bearer_token`: PASSED
  - `test_04_authentication_enforcement`: PASSED
  - `test_07_token_never_logged_or_leaked`: PASSED
  - `test_08_initialize_handshake`: PASSED
  - `test_09_ping_and_notifications`: PASSED
  - `test_10_tools_list_spec`: PASSED
  - `test_11_jev_health_tool`: PASSED
  - `test_12_jev_list_missions_pagination`: PASSED
  - `test_13_jev_get_mission_detail`: PASSED
  - `test_14_jev_get_mission_timeline`: PASSED
  - `test_15_jev_search_capabilities`: PASSED
  - `test_16_jev_create_mission_preview_zero_side_effects`: PASSED
  - `test_17_jev_submit_mission_with_approval_gate`: PASSED
  - `test_18_jev_submit_mission_autopilot_execution`: PASSED
  - `test_19_no_direct_approval_mutation`: PASSED
  - `test_20_mission_pause_resume_cancel_controls`: PASSED
  - `test_21_server_start_stop_clean_lifecycle`: PASSED
  - `test_22_desktop_app_standalone_invariant`: PASSED

### Cross-Phase Combined Regression Suite (Phases 26–29):
- **109 passed / 109 tests** in 32.92 seconds with 0 failures:
  - Phase 26 suite: 20 passed
  - Phase 26.1 audit suite: 15 passed
  - Phase 27 suite: 20 passed
  - Phase 27.1 audit suite: 13 passed
  - Phase 28 suite: 21 passed
  - Phase 29 suite: 20 passed

### Preflight Truth Verification (`preflight.py`):
- **13 pass, 0 fail, 1 warn** (matching verified baseline):
  - App components and config: PASS
  - STT engine warm: PASS (9763 ms)
  - TTS engine functional: PASS (0 ms)
  - Jev decision backend: PASS (1217 ms)
  - Answer provider fallback: PASS (3321 ms)
  - Tier 2 resolver: PASS (100.0 score)
  - Memory engine WAL: PASS (25 ms)
  - Safety engine run-dialog blocking: PASS
  - Secret scan: PASS (zero keys in logs)
  - Dedup & loop breaker guards: PASS
  - Shell blocking: PASS
  - MCP warning: Expected when external Windows automation service binary is not running.

---

## 4. Modified & Created Files

1. `opendots_gateway.py` (New):
   - OpenDots MCP gateway, configuration, loopback HTTP server, and 10 tool handlers.
2. `docs/integrations/OPENDOTS_INTEGRATION.md` (New):
   - Authoritative integration specification and architecture guide.
3. `test_phase29.py` (New):
   - 20 comprehensive unit, integration, and HTTP loopback tests.
4. `config.py` (Modified):
   - Registered `OPENDOTS_GATEWAY_HOST_DEFAULT`, `OPENDOTS_GATEWAY_PORT_DEFAULT`, `OPENDOTS_GATEWAY_ENABLED_DEFAULT`.
5. `assistant_ui.py` (Modified):
   - Registered `opendots_gateway_enabled` (False) and `opendots_gateway_port` (8765) in default settings.
6. `test_phase28.py` (Modified):
   - Added test 21 auditing `MissionCreateDialog` lifecycle and persistence.
