# Hey Jev: Claude Code Working Rules & Development Standards

These rules are mandatory for any AI coding assistant (including Claude Code) working on the Hey Jev repository. Adherence to these principles is essential to maintain codebase integrity and user trust.

---

## Rule 1: Zero Hallucination & Evidence-First Reporting
- **Never claim an action succeeded unless you have verified it.**
- Passing automated unit tests is **NOT** proof that the desktop application works for a human user. Automated tests mock out external audio drivers, focus states, and operating system windows.
- Always provide exact command lines, exit codes, test counts, execution timings, and log excerpts when reporting status.
- Never invent test numbers, commit hashes, or API signatures.

---

## Rule 2: Strict User Data & Git State Preservation
- **NEVER** run recursive deletion commands (`Remove-Item -Recurse -Force`, `rm -rf`, `del /s /q`) on data directories, `%APPDATA%`, or repository folders.
- All persistent user data resides in:
  - `%APPDATA%\HeyJev`
  - `%APPDATA%\hey-jev`
- Never clear databases (`memory.db`, `projects.db`, `hey_jev_missions.db`), settings, logs, or cache directories.
- Always check `git status` before and after modifying files. Do not leave untracked temporary files or uncommitted diffs behind.
- Use `test_cleanup_utils.py` for cleaning up temporary test artifacts.

---

## Rule 3: Reproduce Failures Before Proposing Fixes
- When addressing user-reported issues (such as slow Calculator launching, failed YouTube opening, or speech recognition drops):
  1. Do not immediately rewrite application code.
  2. Write an isolated diagnostic script or trace execution through existing logs.
  3. Formulate a specific, testable hypothesis.
  4. Prove the hypothesis with timed measurements or log outputs before modifying production code.
- If an issue cannot be reproduced, state honestly that it is unverified rather than guessing a solution.

---

## Rule 4: Respect Established Architecture — No Premature Redesigns
- Hey Jev has an established, working three-tier architecture developed across 29 phases.
- **DO NOT** rewrite working subsystems from scratch.
- **DO NOT** replace Python components with JavaScript/Node.js equivalents.
- **DO NOT** invent competing mission coordinators, parallel agent harnesses, or alternative database schemas.
- Implement narrow, targeted improvements that fit seamlessly into existing patterns.

---

## Rule 5: User-Visible Reliability & Latency Over Feature Expansion
- **DO NOT start Phase 30 or introduce new feature phases until existing basic desktop functionality is fast, stable, and verified.**
- Prioritize:
  1. **Latency reduction:** Making common voice commands respond in <500ms.
  2. **Desktop reliability:** Ensuring app launching and window management work consistently without focus drops.
  3. **STT accuracy:** Preventing speech recognition acoustic confusion and dropped commands.
  4. **Human control:** Ensuring the PySide6 UI and Mission Center give the user full visibility and control.

---

## Rule 6: Absolute Secret Hygiene
- **NEVER** commit real API keys, tokens, passwords, or credentials to Git.
- Always verify that `.env` is ignored by `.gitignore`.
- When adding new configuration parameters, document them in `.env.example` with clear placeholder values only.
- Ensure all logging statements pass sensitive text through `safety_engine.py:redact_secrets()`.
- Scan git diffs before committing to ensure no test tokens look like real leaked credentials.

---

## Rule 7: Verification Loop Before Claiming Task Completion
Before completing any engineering task:
1. Run `git status` to verify modified files and ensure no untracked artifacts exist.
2. Run the targeted phase test suite: `.venv\Scripts\pytest.exe test_<phase>.py`.
3. Run the full regression test suite: `.venv\Scripts\pytest.exe -q`.
4. Run the preflight truth suite: `.venv\Scripts\python.exe preflight.py`.
5. Verify that commit messages follow conventional commits (e.g. `feat(phase):`, `fix(core):`, `docs(handoff):`).
