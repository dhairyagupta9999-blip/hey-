# Phase 16 Security & Privacy Architecture

## 1. Executive Summary

Phase 16 equips Hey Jev with persistent memory and project awareness while enforcing strict local-first privacy, least-privilege context handoff, bounded filesystem traversal, and non-destructive historical provenance.

---

## 2. Security Guarantees & Privacy Controls

### 2.1 Local-First Architecture
* **Zero Cloud Memory Leakage**: All memories and project metadata are stored exclusively on local disk via SQLite in WAL mode (`projects.db`, `memory.db`).
* **Zero External Dependencies**: Keyword matching, token indexing, and hybrid scoring (`BM25SemanticProvider`) run in pure Python in-process with zero network overhead and zero third-party cloud daemons.

### 2.2 Sensitivity Classification & Least-Privilege Agent Filtering
Memories are tagged with one of four explicit sensitivity tiers:
1. `PUBLIC`: Public documentation, language syntax, universal knowledge.
2. `NORMAL`: Project-level architecture, build status, common failure lessons.
3. `PRIVATE`: User preferences, local filesystem paths, personal interaction styles.
4. `SENSITIVE`: Internal endpoint configurations, specific development flags.

**Enforcement in ContextBuilder**:
* When packaging context for subordinate agents, `ContextBuilder` inspects `caller_permissions`.
* If the calling agent lacks explicit elevated permissions (e.g. `TaskPermission.READ_FILES`), memories with `PRIVATE` or `SENSITIVE` sensitivity are strictly stripped from the returned context.

### 2.3 Automated Credential Sanitization
To guarantee that user secrets never enter persistent SQLite storage:
* All record contents and summaries pass through `sanitize_content` regex sanitizers upon initialization (`__post_init__`).
* Patterns detected and redacted:
  * Anthropic / OpenAI API keys (`sk-[a-zA-Z0-9_\-]{20,}`)
  * GitHub personal access tokens (`ghp_[a-zA-Z0-9]{30,}`)
  * Google Cloud / Gemini API keys (`AIza[0-9A-Za-z\-_]{35}`)
  * Inline passwords and tokens (`password=...`, `token=...`, `api_key=...`)
  * Bearer authentication tokens (`Bearer [a-zA-Z0-9_\-\.]{20,}`)
* Detected secrets are replaced with `[REDACTED_CREDENTIAL]`.
* Memory records may store safe references (e.g. `{"credential_configured": True}`) but never raw secret strings.

### 2.4 Bounded Filesystem Discovery & Root Drive Protection
`ProjectScanner` enforces strict safety policies against unintended system scanning:
* **Root Drive Sweep Rejection**: Scanning drive roots (e.g. `C:\`, `/`) or Windows system directories (`C:\Windows`, `C:\Program Files`, `APPDATA`, `LOCALAPPDATA`) immediately raises a safety exception.
* **Bounded Traversal**: Recursion depth is capped at `max_depth` (default 3).
* **Trapped Folder Exclusion**: Traversals prune `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, `dist`, and `build` folders in-place.

### 2.5 Safe Read-Only Git Inspection
`GitInspector` provides real-time workspace awareness while guaranteeing state immutability:
* Executes only read-only subcommands: `rev-parse`, `log`, `status --porcelain`.
* Strictly prohibits destructive or state-altering commands (`checkout`, `commit`, `push`, `pull`, `reset`, `branch`).
* Bounded by a 3.0s timeout per invocation to prevent process hanging.

### 2.6 Pruning Invariants & Non-Destructive Provenance
* **Protected Memory Categories**: `USER_PREFERENCE` and `PROJECT_DECISION` records are immune to pruning and can never be purged by automatic maintenance sweeps, regardless of age or importance.
* **Atomic Supersession**: Updating or overriding an existing state does not delete the prior record; it marks the record `SUPERSEDED`, sets `superseded_at` and `superseded_by`, preserving full temporal auditability.
