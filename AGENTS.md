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
