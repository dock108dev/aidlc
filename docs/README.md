# Documentation Index

This directory contains the current operational documentation for the AIDLC CLI.
The repository root intentionally keeps only the short `README.md`.

## Current Guides

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | What AIDLC does today, lifecycle phases, package structure, and data flow. |
| [cli-lifecycle.md](cli-lifecycle.md) | CLI commands, run modes, state transitions, validation, finalization, and generated artifacts. |
| [configuration.md](configuration.md) | Runtime config loading, provider routing, validation, autosync, finalization, and environment variables. |
| [local-development.md](local-development.md) | Local setup, tests, linting, security checks, coverage, and packaging commands. |
| [deployment.md](deployment.md) | Installing and running the CLI from a checkout, pipx, wheel, or CI host. |
| [known-limitations.md](known-limitations.md) | Intentional non-goals and operational constraints. |
| [deprecations.md](deprecations.md) | Removed commands, config keys, and compatibility policy. |

## Packaged Templates

`aidlc/project_template/BRAINDUMP.md` is copied into target repositories by
`aidlc init` when `BRAINDUMP.md` is missing.
