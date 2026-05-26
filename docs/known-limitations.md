# Known Limitations

This page documents current non-goals and operational constraints that are
visible in the codebase.

## Non-Goals

AIDLC intentionally does not:

- run as a server, daemon, worker, or cron scheduler
- deploy target applications
- manage provider billing or login secrets directly
- infer product intent without a root `BRAINDUMP.md`
- preserve removed compatibility branches indefinitely
- treat unstructured provider output as success when there is no supporting
  file diff or test signal
- support validation modes other than `progressive`
- provide a public audit CLI command

## Operational Constraints

- One active lifecycle run per target repository is guarded by
  `.aidlc/run.lock`.
- Generated run state lives under the target repository's `.aidlc/` directory.
- Provider quality, quotas, and CLI availability directly affect run quality.
- Validation quality depends on configured or detected test commands.
- Project type and command detection are heuristic.
- Autosync can commit and push when enabled; it assumes the target repository's
  git remote and auth are usable.

## Automatic Behaviors

Some cleanup happens automatically:

- Fresh non-resume runs archive prior `.aidlc/` working state under
  `.aidlc/_archive/<timestamp>/`.
- Planning dependency graphs are normalized and cycles are broken.
- Implementation ordering can also break dependency cycles to avoid deadlock.
- Validation can create VFIX issues for parsed test failures.
- Finalization can update `.aidlc/config.json` with newly detected commands.

These are part of the current lifecycle, not optional plugins.

## Validation Boundaries

Default validation allows missing tests to be treated as skipped. Production
profile changes that default by setting stricter validation keys unless the user
explicitly overrides them.

Validation does not guarantee production correctness; it runs configured or
detected build/unit/integration/e2e commands and records the result.

## Documentation Boundaries

`BRAINDUMP.md` is target-project input and is not overwritten. AIDLC may update
other Markdown during the `docs` finalization pass because that provider prompt
runs with edit permissions.

Generated discovery and research Markdown under `.aidlc/` is working state, not
canonical user documentation.
