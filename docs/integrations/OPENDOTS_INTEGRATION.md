# Hey Jev OpenDots Interoperability Integration Guide

This document defines the interoperability specification and boundary between **Hey Jev** and **OpenDots** (CopilotKit AI coworkers platform).

---

## 1. Confirmed OpenDots Source Patterns

Based on inspection of the official OpenDots repository (`CopilotKit/OpenDots`):

1. **Architecture & Scope**:
   - OpenDots is an open-source template for persistent AI agents, each with an optional containerized computer (OpenBot).
   - Features include Spaces (document workspaces), specialist Dots (agents with instructions, roles, and tool permissions), human-in-the-loop review cards in chat, and external Connections.
   - External services and tools connect via the **Model Context Protocol (MCP)**.
   
2. **MCP Tool Integration**:
   - Dots obtain external tools by connecting to standard MCP servers (stdio or SSE/HTTP).
   - OpenDots classifies tools into:
     - **Read-only tools**: Run autonomously without explicit human confirmation.
     - **Mutating / Action tools**: Pause for a human review card ("Approve & run" / "Decline") before execution.
   - Tool registration follows standard MCP JSON-RPC 2.0 schemas (`tools/list`, `tools/call`, `initialize`, `ping`).

3. **Multi-Agent & Conversation Boundaries**:
   - OpenDots manages conversation threads using CopilotKit Intelligence and SQLite metadata.
   - It is an external interface and collaboration space; it is **not** an authoritative local Windows automation engine.

---

## 2. Implemented Hey Jev MCP Gateway Architecture

### Core Invariant
**Hey Jev remains the authoritative agent engine. OpenDots is an optional external workspace and interface.**

Hey Jev already provides:
- Goal interpretation (`MissionInterpreter`)
- Dependency-aware topological DAG planning (`MissionPlanner`)
- Durable SQLite persistence and restart recovery (`SqliteMissionStore`)
- Task execution, retries, and outcome verification (`MissionCoordinator`)
- Concurrency and hardware resource governance on dual-core CPU (`ResourceGovernor`)
- Native Windows UI and human approval gates (`MissionCenterWindow`, `MissionCreateDialog`)
- Operational health tracking and incident diagnosis (`OperationalHealthCoordinator`, `FailureDiagnosisEngine`)

The OpenDots Gateway (`opendots_gateway.py`) exposes a controlled, authenticated MCP surface to allow OpenDots specialists to inspect Jev status, search capabilities, preview missions, and submit mission requests without bypassing Jev's native safety boundaries.

### Network and Security Architecture

```
+-------------------------------------------------------+
|                      OpenDots                         |
|   (Specialist Dots, Spaces, Chat, Human Review Cards) |
+-------------------------------------------------------+
                           |
                           | Authenticated HTTP/JSON-RPC (Bearer Token)
                           v
+-------------------------------------------------------+
|            Hey Jev OpenDots MCP Gateway               |
|            (127.0.0.1 Loopback Only, Bounded)         |
+-------------------------------------------------------+
  |              |              |              |
  v              v              v              v
Mission       Mission       Capability     Operational
Store       Coordinator      Registry        Health
```

1. **Loopback Only (`127.0.0.1`)**:
   - The gateway server binds strictly to IPv4 loopback (`127.0.0.1`). External network interfaces are rejected by policy.
2. **Mandatory Bearer Token Authentication**:
   - All incoming requests must supply an HTTP header `Authorization: Bearer <token>`.
   - The token is loaded from `JEV_OPENDOTS_BEARER_TOKEN` or secure settings; it is never hardcoded and never logged.
   - Comparison is performed using constant-time verification (`secrets.compare_digest`).
   - Missing or invalid tokens immediately yield HTTP 401 Unauthorized.
3. **Disabled by Default**:
   - The gateway is completely disabled by default (`enabled=False`). No listening sockets or background threads are spawned unless explicitly enabled by configuration.
4. **Secret Scrubbing**:
   - All returned data passes through `memory_model.sanitize_content`, redacting API keys, passwords, and sensitive tokens.

### Exposed MCP Tools

The gateway exposes exactly 10 authorized tools divided into two security tiers:

#### Tier 1: Read-Only Tools (Autonomous Query)
1. **`jev_health`**:
   - Returns operational health observations, memory footprint, CPU load, and resource governor slot occupancy.
2. **`jev_list_missions`**:
   - Returns paginated, safe summaries of existing missions (`mission_id`, `objective`, `status`, `mode`, `progress`, `created_at`, `updated_at`).
3. **`jev_get_mission`**:
   - Returns full details of a specific mission, including DAG tasks, acceptance criteria, and verification evidence.
4. **`jev_get_mission_timeline`**:
   - Returns bounded, chronological event records for a mission.
5. **`jev_search_capabilities`**:
   - Searches registered capabilities (tools and skills) without executing them.

#### Tier 2: Governed Mission Tools (Controlled Actions)
6. **`jev_create_mission_preview`**:
   - Accepts a natural language objective and produces a normalized goal, acceptance criteria, and candidate DAG task graph.
   - **Zero side-effects**: Does NOT save to database, does NOT dispatch tasks, does NOT execute.
7. **`jev_submit_mission`**:
   - Submits a user-approved mission into Jev's authoritative lifecycle (`MissionInterpreter`, `MissionPlanner`, and `SqliteMissionStore`).
   - Respects `MANUAL`, `COPILOT`, and `AUTOPILOT` modes.
   - If any task has an approval checkpoint or risk level $\ge$ HIGH, or mode is MANUAL/COPILOT, the mission transitions to `WAITING_FOR_APPROVAL` or `READY` and **does NOT auto-execute**.
8. **`jev_request_pause`**:
   - Calls `coordinator.pause_mission(mission_id)`.
9. **`jev_request_resume`**:
   - Calls `coordinator.resume_mission(mission_id)`.
10. **`jev_request_cancellation`**:
    - Calls `coordinator.cancel_mission(mission_id, reason=...)`.

### Safety Invariants
- **No Generic Tool Execution**: There is no generic `execute_tool` or `run_command` MCP endpoint.
- **No Direct Approval Mutation**: External callers cannot force approval of tasks or mutate `approved_tasks`. All approvals must go through Jev's native human verification.
- **No Shell Execution**: The gateway exposes only structured mission operations.

---

## 3. Test-Verified Capabilities

The Phase 29 test suite (`test_phase29.py`) formally verifies:
1. **Disabled by default**: Gateway does not start without explicit configuration.
2. **Loopback security**: Rejects attempts to bind to public or non-loopback addresses.
3. **Authentication enforcement**: Unauthenticated requests, empty tokens, and invalid tokens receive 401 Unauthorized; valid tokens succeed.
4. **MCP Protocol Compliance**: `initialize`, `ping`, `tools/list`, and `tools/call` conform strictly to JSON-RPC 2.0.
5. **Safe Read-Only Operations**:
   - `jev_health` returns structured subsystem status and psutil metrics.
   - `jev_list_missions` paginates correctly with safe fields.
   - `jev_get_mission` returns full DAG and criteria structure.
   - `jev_get_mission_timeline` returns bounded events.
   - `jev_search_capabilities` queries registry without execution.
6. **Side-Effect-Free Previews**:
   - `jev_create_mission_preview` returns DAG plan and criteria with zero DB changes and zero execution.
7. **Mission Submission & Governance**:
   - `jev_submit_mission` creates durable missions in `SqliteMissionStore`.
   - Missions requiring approval halt at `WAITING_FOR_APPROVAL` or `READY` and never execute automatically.
8. **Lifecycle Control**:
   - Pause, resume, and cancellation work reliably across valid and invalid states.
9. **Secret Scrubbing**:
   - Bearer tokens, API keys, and sensitive environment strings are scrubbed by `sanitize_content`.
10. **Standalone Invariant**:
    - Disconnection or absence of OpenDots has zero effect on Jev's desktop application or 7-tab UI.

---

## 4. Live-Instance Verifications and Environment Constraints

1. **Hardware & Environment Target**:
   - Target machine: Intel Core i3-5005U (2 physical cores @ 2.00 GHz), 8 GB RAM, Windows 10/11 x64.
   - Zero added external dependencies: Gateway is implemented using standard Python library (`http.server`, `socketserver`, `json`, `threading`) avoiding bulky Node.js or third-party MCP SDK memory overhead.
2. **Local Loopback Verification**:
   - Gateway binds to `127.0.0.1:<port>` with high responsiveness (< 15 ms roundtrip for tool calls).
3. **External OpenDots Deployment**:
   - OpenDots web application (Node.js/Next.js) running locally or in Docker connects to Jev as a standard MCP server.
   - If OpenDots is stopped or unreachable, Jev operates completely unaffected.

---

## 5. Unverified Assumptions & Future Extensions

1. **Bidirectional Webhook Callbacks**:
   - Currently, OpenDots polls Jev via `jev_get_mission` or SSE. Streaming push notifications via webhooks can be added in a future phase if required.
2. **Multi-Tenant Dot Role Partitioning**:
   - Current gateway verifies the bearer token for the entire OpenDots workspace. Future work could assign distinct scoped tokens per specialist Dot.
3. **OpenBot Container Filesystem Sharing**:
   - OpenDots containerized computers (OpenBot) currently maintain isolated filesystems. Jev does not mount OpenBot container filesystems directly.
