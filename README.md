# Eyal Espresso Server Simulator

## Session Release Notes

- Last released version in git: `0.1.0`
- Release commit: pending initial repository commit
- Version numbering reminder for release notes:
  - `X`: major architecture or feature-set changes
  - `Y`: minor functionality additions and bug-fix milestones
  - `Z`: patch/sub-version increments after accepted successful verification cycles
- Current intent:
  - simulate the Gaggia controller side of the system
  - expose operator and test controls through a FastAPI-based interface
  - communicate with the client side over a USB serial COM interface

## Project Workflow Rules

- Product requirements and application design shall be maintained in `docs/EyalEspressoServerSimulatorRequirements and Design.docx`.
- `README.md` is the workflow/session handoff file; the requirements/design document is the primary place for application requirements, UX intent, architecture decisions, and planned features.
- Every time Codex opens and reviews `docs/EyalEspressoServerSimulatorRequirements and Design.docx`, Codex must update the document field `Reviewed on` with the current time.
- After any documentation change in this repository, Codex must ask Eyal whether to open the `docs` folder.
- Design documentation must be maintained in dual format:
  - human review artifacts in `.docx`
  - machine-readable architecture sources under `docs/architecture/`
- The required machine-readable architecture sources are:
  - `docs/architecture/transport_state_machine.mmd`
  - `docs/architecture/packet_flows.mmd`
  - `docs/architecture/failure_modes.mmd`
  - `docs/architecture/transport_contract.md`
- The text-based architecture sources are the canonical editable design source for workflow/state/packet behavior; rendered diagrams and `.docx` content must match them.
- When workflow, state machines, packet definitions, failure handling, ownership, timing, watchdog rules, or transport architecture change, update both:
  - the relevant `.docx` design documents
  - the matching files under `docs/architecture/`
- Do not maintain image-only diagrams as the sole source of truth. Every important workflow/state/failure diagram must also exist as text-based Mermaid and as structured tables in markdown.
- Use exact code-facing names in documentation for states, packet types, counters, modules, and events. Do not rename concepts in prose if the code uses a different identifier.
- Every state machine must be documented with:
  - purpose and scope
  - state list
  - transition diagram
  - transition table with current state, trigger, guard/condition, action, next state, and timeout/failure behavior
- Every packet flow must be documented with:
  - packet purpose
  - sender and receiver
  - required fields
  - normal response
  - timeout rule
  - error handling
- Every transport contract must explicitly document ownership of:
  - liveness counters such as `HostLiveInteger` and `DeviceLiveInteger`
  - CRC/checksum validation
  - reconnect behavior
  - watchdog enforcement
  - entry to `error`, `reset`, and `initialize`
- If Eyal edits `.docx` files manually, Codex must review those edits and update the text-based files under `docs/architecture/` so future LLM work remains aligned.
- If Codex updates the text-based architecture files first, Codex must also update the corresponding `.docx` documents before considering the documentation change complete.
- Once Eyal establishes manual formatting in a `.docx` document, future `.docx` edits must preserve the existing headings, styles, bullets, numbering, fonts, tables, figure placement, and general layout unless Eyal explicitly asks to change them.
- After manual formatting exists, do not replace the entire `.docx` as a regeneration strategy for normal documentation updates. Prefer targeted in-place OpenXML edits that preserve the existing presentation layer.
- Limit PowerShell command payloads to at most 7000 characters. If a change would require a longer command, split it into smaller commands or use repo-local scripts/files so the command fits reliably within host/tooling limits.
- Repository version is tracked in root `VERSION` with format `X.Y.Z`.
- `X`: major functionality/refactoring changes.
- `Y`: minor bug-fix and incremental functionality changes.
- `Z`: sub-version increment for accepted successful local verification cycles.
- After a successful local verification cycle, Codex must ask whether to commit current changes and bump `Z`.
- After a successful client build+flash or simulator verification cycle that is meant to be exercised through the simulator UI, Codex must automatically run the simulator UI and ask Eyal whether it loaded successfully.
- Every version bump must add a new entry to docs/REVISION_HISTORY.doc that includes the new version number, a timestamp, and a brief description of what changed relative to the previous version.
- Maintain `docs/REVISION_HISTORY.doc` with sections grouped by `X.Y`, a short change summary per entry, and a continuously maintained latest-version feature list.
- Before informing Eyal to run a build, review the VS Code `PROBLEMS` panel and resolve all reported issues.
- After every code change, Codex must perform local update/verification itself before reporting ready:
  - refresh project metadata (`reconfigure` / `compile_commands.json`)
  - run a local build
  - fix all detected issues before asking Eyal to build
- Every function declaration and definition must have a short header comment block with `@brief`, `@details`, parameters, and return value where applicable.
- Every `README.md` change must be committed immediately.
- Repositories must not share tracked files. If another repository needs the same asset, script, or document, duplicate it into that repository and maintain the copies separately.
- When a successful local verification cycle completes, play the project celebration sound from `sounds\build-success-monkey-1p5x.wav`.
- After the success sound for a successful local verification/build cycle, Codex must automatically start the simulator application from the repository virtual environment first, confirm that the backend is actually running, and only then open the simulator web UI.
- Every simulator application launch during Eyal's testing must run with event and logger monitoring enabled so the UI and backend collect actionable runtime data while Eyal exercises the system.
- For this project, the startup order is mandatory:
  - launch the repository `.venv`-backed Python application first
  - verify successful startup with a concrete runtime signal such as a healthy process plus a successful `/health` response
  - only after that open the browser/UI
  - do not open the browser optimistically before backend startup is confirmed
- When waiting for Eyal to do anything required to continue, including replying to a prompt, answering a question, approving a request, or simply not sending a new instruction while Codex is otherwise idle, play the project wait sound from `sounds\WaitSound.wav`.
- For any such waiting state, play `sounds\WaitSound.wav` once immediately when the wait begins, then if 3 minutes pass without a response from Eyal, play it again and keep repeating it every additional 3 minutes until a response arrives or the task resumes.
- Wait-sound playback is a best-effort local notification only. Codex can verify that the helper scripts start and stop successfully, but cannot verify that Eyal actually heard audio on the active output device.
- Session hook for the wait sound:
  - immediately before sending an explicit chat prompt or question that requires Eyal to respond in the conversation, run `scripts\start_wait_sound.ps1`
  - immediately after Eyal responds, run `scripts\stop_wait_sound.ps1`
  - do not rely on the wait sound for hidden tool-approval popups, internal sandbox approval flows, or other non-chat waits because Eyal may not hear or notice those cases
- Important inconsistencies, mismatches, or stale notes discovered during work must be explicitly pointed out before they are forgotten.
- UI spacing rule: keep at least `10` pixels of spacing between menus, buttons, and adjacent interactive controls unless a specific screen explicitly requires otherwise.
- This simulator is intended to own exactly one serial port endpoint at a time. Do not design the runtime so multiple processes compete for the same COM device.
- For USB serial integration, one background serial manager shall own the COM port and the FastAPI routes shall communicate with that manager instead of opening the port directly from request handlers.

### Codex and VS Code `PROBLEMS` (Session Rule)

- Codex currently cannot directly read the live VS Code `PROBLEMS` UI panel state by itself in-session.
- Therefore, Codex must use task/build output plus problem matchers as the machine-readable source of diagnostics.
- Required workflow for every coding session:
  - Run the local simulator build/verification commands.
  - Verify zero active problems from task output and fix all issues before saying build-ready.
  - If UI-only diagnostics still appear, Eyal should paste the `PROBLEMS` entries and Codex must resolve them before proceeding.
- VS Code tasks should reveal problems on build failure and use problem matchers so diagnostics remain machine-readable.

## Recommended Architecture Baseline

- Runtime stack:
  - Python
  - FastAPI
  - pyserial
  - uvicorn
- Suggested module split:
  - `server/api`: REST and websocket endpoints
  - `server/transport`: serial port ownership and framing
  - `server/sim`: controller state machine and protocol behavior
  - `tests`: protocol and API tests

## Expected Repository Files

- `README.md`: workflow and session handoff
- `AGENTS.md`: repo-specific Codex rules
- `VERSION`: canonical project version
- `docs/EyalEspressoServerSimulatorRequirements and Design.docx`: requirements and design document
- `docs/EyalEspressoServerSimulatorDetailedDesign.docx`: detailed design document
- `docs/REVISION_HISTORY.doc`: revision history and latest feature list
- `docs/VERSIONING.md`: versioning policy
- `scripts/generate_requirements_docx.ps1`: requirements document generator
- `scripts/generate_detailed_design_docx.ps1`: detailed design generator
- When creating or updating `.docx` files programmatically, use an extract/edit/repack flow for the OpenXML container (`.docx` is a ZIP package) instead of in-place ZIP entry replacement on this host.
- Programmatic `.docx` generation must write valid OpenXML package entry names with forward slashes such as `_rels/.rels` and `word/document.xml`, and must emit valid XML text without doubled quote escaping inside the stored XML files.
- `scripts/play_wait_sound.ps1`: one-shot WAV playback helper
- `scripts/start_wait_sound.ps1`: immediate and repeating wait-sound worker starter
- `scripts/stop_wait_sound.ps1`: wait-sound worker stop helper
- `sounds/build-success-monkey-1p5x.wav`: local build-success sound asset
- `sounds/WaitSound.wav`: local runtime wait-sound asset

## Initial Development Notes

- The current client project uses `COM9` for flashing and runtime interaction. The simulator must not try to own the same physical port at the same time as flashing or client-side serial tools.
- If both sides need to run on one PC simultaneously, use a virtual COM pair or a separate serial bridge path.
