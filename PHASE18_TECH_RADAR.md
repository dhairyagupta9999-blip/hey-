# Phase 18: Multi-Agent Swarm & Provider Gateway Technology Radar

## 1. Executive Summary

Phase 18 introduces real multi-agent swarms and an extensible provider gateway into Hey Jev. The core engineering mandate is:
> **"Use the minimum number of specialists necessary to solve the user's goal faster and better."**

Given the production hardware constraints (Intel Core i3-5005U, 2 physical cores, ~8 GB RAM, Windows 10 Pro x64), Hey Jev cannot afford to run multiple concurrent heavy local agents, heavyweight message brokers, or high-overhead Python multi-agent frameworks. This radar systematically evaluates existing industry frameworks against our performance, Windows desktop, privacy, and architectural requirements.

---

## 2. Evaluation Matrix

| Framework / Standard | License | Maintenance | Windows & Py 3.11 | State Mgmt | Remote & Handoffs | Local Resource Overhead | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A2A (Agent-to-Agent 1.0)** | Apache 2.0 | High (Standard) | Excellent | Declarative | Native A2A cards & envelopes | Very Low (< 1 MB) | **ADOPT (Core Standard)** |
| **OpenAI Agents SDK / Swarm** | MIT | Active | Good | Ephemeral | Handoff functions | Low | **STUDY & ADAPT** |
| **LangGraph** | MIT | Very High | Good | Graph / State | State channels | Medium-High (Heavy deps) | **DEFER (Too heavy)** |
| **CrewAI** | MIT | High | Moderate | Sequential / Hierarchical | Process manager | High (Pydantic/LangChain bloat) | **REJECT (High CPU/RAM)** |
| **Microsoft Agent Framework / AutoGen**| MIT | High | Good | Conversational | Multi-agent dialogue | High (Heavy token/memory loop)| **DEFER** |
| **PydanticAI** | MIT | Very High | Excellent | Type-safe | Model agnostic | Low-Medium | **STUDY (Type patterns)** |
| **OpenHands / Agent Server** | MIT | High | Linux-first | Docker/Server | Client-server | Very High (Requires Docker) | **REJECT (Linux/Docker lock)** |
| **OpenAI-Compatible API Spec** | Open Standard | Universal | Native | Stateless / Session | REST endpoints | Negligible (HTTP client) | **ADOPT (Primary Transport)**|
| **Anthropic Messages API Spec** | Proprietary | High | Native | Stateless | REST endpoints | Negligible (HTTP client) | **ADOPT (Adapter Spec)** |
| **Ollama / Local OpenAI Endpoint** | MIT / Go | Very High | Excellent on Win | Stateless / Local | Local HTTP daemon | Modest (< 50 MB client) | **ADOPT (Local Model Gateway)**|
| **Lightweight In-Process Swarm Engine**| In-house | Dedicated | Native Windows | SQLite WAL + DAG | Task / Worker / Message | Microsecond (< 2 MB RAM) | **ADOPT (Jev Swarm Core)** |

---

## 3. Detailed Framework Assessments

### 3.1 A2A (Agent-to-Agent 1.0)
- **Status**: **ADOPT**
- **Rationale**: A2A 1.0 provides a clean, production-oriented specification for cross-vendor agent interoperability. It specifies:
  - Agent Cards (capabilities, modalities, authentication requirements).
  - Standardized message envelopes (`TASK_REQUEST`, `TASK_RESULT`, `HANDOFF`, `ARTIFACT_REFERENCE`).
  - Clear architectural separation: **A2A governs Agent-to-Agent communication**, while **MCP governs Agent-to-Tool execution**.
- **Implementation in Jev**: Hey Jev aligns its `AgentCapabilityCard`, `AgentMessage`, and remote worker transport directly with A2A 1.0 primitives.

### 3.2 OpenAI Agents SDK & Lightweight Swarm Patterns
- **Status**: **STUDY & ADAPT**
- **Rationale**: OpenAI's Swarm pattern pioneered clean, explicit agent handoffs (`handoff(agent) -> result`) and role-based instruction isolation. However, the reference library is coupled to OpenAI-specific client instances and lacks durable DAG persistence, local resource governance, and deterministic verifier loops.
- **Implementation in Jev**: We adapt the explicit handoff and dynamic role generation concepts while preserving Jev's provider-neutral architecture and local resource gating.

### 3.3 LangGraph
- **Status**: **DEFER**
- **Rationale**: LangGraph offers robust graph-based state transitions and persistence. However, bringing LangGraph into Hey Jev introduces hundreds of megabytes of third-party dependencies (`langchain-core`, heavy async event loops), which slows down startup times and competes with Whisper STT for RAM on our 8 GB hardware.
- **Decision**: Implement our own zero-dependency DAG execution engine (`SwarmEngine`) backed by SQLite WAL mode and Python's standard library `concurrent.futures`.

### 3.4 CrewAI
- **Status**: **REJECT**
- **Rationale**: CrewAI imposes heavy abstractions, rigid role hierarchies, and extensive background memory/telemetry layers that produce high idle CPU usage and unpredictable token burns. Its multi-agent loops frequently trigger infinite recursion if not tightly constrained.
- **Decision**: Rejected for production desktop use on dual-core hardware.

### 3.5 OpenHands / Agent Server / Agent Canvas
- **Status**: **REJECT for Core Windows Runtime**
- **Rationale**: OpenHands relies primarily on Docker containers and Linux environments. On Windows 10 without WSL2/Hyper-V enabled for Jev, Docker execution is unavailable or introduces massive overhead.
- **Decision**: Retain Jev's direct Windows desktop automation and in-process lightweight orchestration.

### 3.6 OpenAI-Compatible & Anthropic API Transports
- **Status**: **ADOPT**
- **Rationale**: Over 95% of cloud and self-hosted inference providers (OpenRouter, DeepSeek, Groq, Together, vLLM, LM Studio, Ollama) conform to the OpenAI REST specification. Providing a clean, zero-dependency, streaming-capable HTTP transport adapter allows Jev to route to virtually any model provider with zero vendor lock-in.

---

## 4. Key Architectural Decisions

1. **Local vs. Remote Worker Asymmetry**:
   - `max_local_agents = 1`: Local machine runs at most one active CPU-intensive agent or task at a time.
   - Remote workers execute in cloud or external environments; the local machine acts strictly as coordinator, collector, synthesizer, and verifier.
2. **Minimum-Sufficient-Worker Heuristic**:
   - Swarms do not spawn workers blindly. Tasks are evaluated by complexity:
     - Simple requests $\rightarrow$ 1 worker.
     - Moderate tasks $\rightarrow$ 2 workers (e.g. Worker + Reviewer).
     - Complex research/diagnostics $\rightarrow$ 3 workers (e.g. 2 Researchers + Synthesizer).
   - `max_spawn_depth = 1`: Agents cannot recursively spawn swarms of sub-agents.
3. **Multi-Strategy Execution**:
   - Provide explicit, deterministic orchestration topologies: `SEQUENTIAL`, `PARALLEL`, `PIPELINE`, `DEBATE`, `SPECIALIST_PANEL`, `MAP_REDUCE`, `COMPETITION`, and `REVIEW_LOOP`.
4. **Free-First Cost Policy**:
   - `COST_MODE` options: `FREE_FIRST`, `BALANCED`, `QUALITY_FIRST`, `LOCAL_ONLY`, `OFFLINE_ONLY`.
   - If `max_cost = 0`, cloud paid APIs are blocked with `BUDGET_EXCEEDED` or `WAITING_FOR_APPROVAL`.
