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
- Repository version is tracked in root `VERSION` with format `X.Y.Z`.
- `X`: major functionality/refactoring changes.
- `Y`: minor bug-fix and incremental functionality changes.
- `Z`: sub-version increment for accepted successful local verification cycles.
- After a successful local verification cycle, Codex must ask whether to commit current changes and bump `Z`.
- Maintain `docs/REVISION_HISTORY.doc` with sections grouped by `X.Y`, a short change summary per entry, and a continuously maintained latest-version feature list.
- Every function declaration and definition must have a short header comment block with `@brief`, `@details`, parameters, and return value where applicable.
- Every `README.md` change must be committed immediately.
- Repositories must not share tracked files. If another repository needs the same asset, script, or document, duplicate it into that repository and maintain the copies separately.
- When a successful local verification cycle completes, play the project celebration sound from `sounds\build-success-monkey-1p5x.wav`.
- When waiting for Eyal to do anything required to continue, including replying to a prompt, answering a question, approving a request, or simply not sending a new instruction while Codex is otherwise idle, play the project wait sound from `sounds\WaitSound.wav`.
- For any such waiting state, play `sounds\WaitSound.wav` once immediately when the wait begins, then if 3 minutes pass without a response from Eyal, play it again and keep repeating it every additional 3 minutes until a response arrives or the task resumes.
- Session hook for the wait sound:
  - immediately before sending a prompt, question, or approval request that requires Eyal to respond, run `scripts\start_wait_sound.ps1`
  - immediately after Eyal responds, run `scripts\stop_wait_sound.ps1`
- Important inconsistencies, mismatches, or stale notes discovered during work must be explicitly pointed out before they are forgotten.
- UI spacing rule: keep at least `10` pixels of spacing between menus, buttons, and adjacent interactive controls unless a specific screen explicitly requires otherwise.
- This simulator is intended to own exactly one serial port endpoint at a time. Do not design the runtime so multiple processes compete for the same COM device.
- For USB serial integration, one background serial manager shall own the COM port and the FastAPI routes shall communicate with that manager instead of opening the port directly from request handlers.

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
- `scripts/play_wait_sound.ps1`: one-shot WAV playback helper
- `scripts/start_wait_sound.ps1`: immediate and repeating wait-sound worker starter
- `scripts/stop_wait_sound.ps1`: wait-sound worker stop helper
- `sounds/build-success-monkey-1p5x.wav`: local build-success sound asset
- `sounds/WaitSound.wav`: local runtime wait-sound asset

## Initial Development Notes

- The current client project uses `COM9` for flashing and runtime interaction. The simulator must not try to own the same physical port at the same time as flashing or client-side serial tools.
- If both sides need to run on one PC simultaneously, use a virtual COM pair or a separate serial bridge path.


