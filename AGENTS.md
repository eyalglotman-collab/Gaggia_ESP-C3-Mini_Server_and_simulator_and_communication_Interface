# Project Rules

1. Verification Commit Prompt
- After every accepted successful local verification cycle, ask whether to commit current changes and create a sub-version release.

2. Versioning Scheme
- The project version uses `X.Y.Z` stored in the `VERSION` file.
- `X` (major): functionality additions/removals and refactoring-level changes.
- `Y` (minor): bug fixes and smaller functionality changes.
- `Z` (patch/sub-version): increment on every accepted verification cycle.

3. Function and Header Documentation
- Every function declaration and definition must have a short header comment block.
- The block must include at least:
  - Purpose (`@brief`)
  - Implementation notes (`@details`)
  - Parameters and return value where applicable

4. Revision Document
- Maintain `docs/REVISION_HISTORY.doc` with:
  - Sections grouped by major/minor revisions (`X.Y`)
  - A short change summary per entry
  - A continuously maintained `Latest Version Feature List` section

5. Rule-Gated Verification/Build/Flash Sequence (Mandatory)
- For any verification/build/flash request, execute this gated sequence and report each gate completion:
  - Gate 1: quick compliance check of `AGENTS.md`, `README.md`, and `CLAUDE.md`.
  - Gate 2: run `scripts/start_wait_sound.ps1`.
  - Gate 3: execute requested steps sequentially only (never parallel flash).
  - Gate 4: run `scripts/stop_wait_sound.ps1`.
  - Gate 5: on success, run `scripts/play_build_success_sound.ps1`.
- Post-flash monitor capture is optional and only required when explicitly requested.
- If any gate fails, stop immediately and report: `RULE-GATED SEQUENCE BROKEN: <gate>`.
