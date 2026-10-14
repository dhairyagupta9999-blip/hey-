# Phase 19: Jev Reliability, Verification, Recovery & Undo Technology Radar

## 1. Executive Summary

Phase 19 provides the resilience and safety foundation for Hey Jev. In complex desktop, web, multi-agent, and coding workflows, failures will inevitably happen: processes crash, networks drop, APIs rate-limit, and operations encounter unexpected environments.

The architectural mandate is:
> **"Do not just execute. Understand state, checkpoint state, verify state, recover from failure, undo when safe, reconcile after crash, and stop safely when recovery is not possible."**

Given the dual-core **Intel Core i3-5005U** CPU and 8 GB RAM constraints, Hey Jev cannot run heavy distributed coordination daemons (e.g., Temporal server clusters, ZooKeeper, Redis, or Kafka). Reliability must be **in-process, local-first, low-overhead, and deterministic**, backed by SQLite WAL mode and explicit state verification.

---

## 2. Technology Evaluation Matrix

| Framework / Pattern | License | Maintenance | Windows & Py 3.11 | Durability / State Model | Local Hardware Overhead | Suitability for Hey Jev | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Temporal.io** | MIT / BSL | Very High | Requires Server | Event history replay | Very High (Go/Java cluster + DB) | Unsuitable for offline desktop | **REJECT** |
| **Microsoft Durable Task (Durable Functions)** | MIT | High | Good (gRPC SDK)| Checkpoint & replay | High (Requires backend daemon) | Useful as architectural reference | **STUDY & ADAPT** |
| **Saga Orchestration & Compensation** | Architecture Pattern | Universal | Native | Compensating actions | Negligible (< 100 KB) | Perfect for desktop mutations | **ADOPT (Core Pattern)** |
| **Transactional Outbox / Write-Ahead Log (WAL)** | Architecture Pattern | Universal | Native (SQLite) | Pre-action recording | Microsecond-level (< 200 KB) | Rock-solid crash safety | **ADOPT (Core WAL)** |
| **Circuit Breakers (Resilience4j / pybreaker pattern)**| Architecture Pattern | Active | Native | Closed / Open / Half-Open | Nanosecond-level | Essential for AI providers & tools| **ADOPT** |
| **Exponential Backoff with Full Jitter** | Standard (RFC) | Universal | Native | Bounded sleep schedule | Negligible | Essential for rate limits & network | **ADOPT** |
| **Semantic Resource Locking** | In-house Pattern | Dedicated | Native | In-memory + SQLite leases | Negligible | Prevents concurrent mutation conflicts| **ADOPT** |
| **Tamper-Evident Hash Chaining** | Standard (Merkle/Chain)| Universal | Native (hashlib) | $H_n = \text{hash}(H_{n-1} + R_n)$ | Negligible (< 1 ms per event) | Auditable execution history | **ADOPT** |
| **LangGraph Checkpointing** | MIT | High | Good | State snapshots per node | High dependencies | Incompatible with zero-dep core | **DEFER** |

---

## 3. Detailed Framework & Architectural Assessments

### 3.1 Temporal vs. Microsoft Durable Task Framework
- **Temporal**: Requires hosting a Temporal Server cluster (written in Go/Java, needing Cassandra/PostgreSQL/MySQL), which consumes 500+ MB of RAM and requires background daemons. This violates our offline, single-machine desktop design.
- **Microsoft Durable Task**: Features a clean replay-based execution model where orchestrators execute deterministically and checkpoint after every yield or task completion. While the full framework requires a storage provider (Azure Storage or emulator), its **checkpoint boundary and deterministic step identity model** directly informs Jev's checkpointing architecture.
- **Verdict**: Reject Temporal server. Adapt the Durable Task checkpoint/replay concept into an in-process, SQLite WAL-backed checkpoint engine.

### 3.2 Saga Compensation Patterns for Desktop Automation
- **Concept**: Distributed transactions cannot rely on two-phase commit (2PC) across arbitrary systems (e.g. filesystem + external API + window control). Sagas model a transaction as a series of steps:
  $$T = [A_1, A_2, \dots, A_n]$$
  accompanied by compensating transactions:
  $$C = [C_1, C_2, \dots, C_{n-1}]$$
  If step $A_k$ fails and cannot be recovered, the system executes $C_{k-1}, \dots, C_1$ in reverse order.
- **Critical Caveat**: **Not every action is reversible.** A file move is reversible ($\text{move back}$); an API purchase or email dispatch is irreversible ($\text{IRREVERSIBLE}$). Jev explicitly classifies actions as `REVERSIBLE`, `PARTIALLY_REVERSIBLE`, or `IRREVERSIBLE`.

### 3.3 Write-Ahead Logging & Pre-Action Intent Recording
- If Hey Jev crashes in the middle of executing a high-risk mutation (e.g., during a file move or package installation), upon reboot it must know what it intended to do.
- By persisting an `ActionRecord` with status `ATTEMPTED` and `idempotency_key` *before* issuing the mutation, the `ReconciliationEngine` on startup can inspect the environment, compare expected vs. observed state, and determine if the mutation was `ALREADY_APPLIED`, `FAILED`, or in an `UNKNOWN_STATE`.

### 3.4 Circuit Breakers
- When an external model provider (e.g., an OpenAI-compatible endpoint or remote A2A worker) repeatedly throws timeouts or 429 rate limits, continuing to flood it wastes user compute and CPU cycles.
- A 3-state circuit breaker (`CLOSED` $\rightarrow$ `OPEN` $\rightarrow$ `HALF_OPEN`) temporarily trips the failing provider, immediately diverting traffic to an alternative provider or halting cleanly.

---

## 4. Key Architectural Decisions for Phase 19

1. **Lightweight In-Process Engine**: Zero external servers, daemons, or heavy dependencies. All state, checkpoints, and action logs live in SQLite WAL mode in `%APPDATA%\HeyJev\heyjev_reliability.db`.
2. **Explicit `UNKNOWN_STATE`**: Never confuse an unconfirmed operation with a confirmed failure. If a network drops or a process crashes mid-action, the state is `UNKNOWN_STATE` until the `ReconciliationEngine` proves what actually happened.
3. **Verification Levels 0 through 5**: Stop relying on agent self-reporting ("I fixed the bug"). Require objective evidence (disk inspection, process checks, deterministic tests) scaled to the risk of the operation.
4. **UndoManager with Previews**: Support non-destructive "Undo that" by reversing operations in dependency order, with explicit pre-flight previews and hard refusals on irreversible actions.
5. **Tamper-Evident History**: Chained cryptographic hashes ($H_n = \text{SHA256}(H_{n-1} + R_n)$) guarantee that historical actions and compensations cannot be silently altered or deleted.
