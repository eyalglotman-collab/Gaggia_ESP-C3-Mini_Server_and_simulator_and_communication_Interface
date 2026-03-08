# Versioning Policy

## Format
- Version format: `X.Y.Z`

## Meaning
- `X` (major): architecture changes and large feature-scope changes.
- `Y` (minor): bug fixes and incremental functionality additions.
- `Z` (patch/sub-version): accepted successful verification cycles.

## Operational Rule
- After each successful local verification cycle, confirm whether to:
  1. commit code changes
  2. create a sub-version (`Z = Z + 1`)

## Current Version Source
- Canonical version is stored in repository root file: `VERSION`
