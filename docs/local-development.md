# Local Development

## Prerequisites

- Python 3.11+
- At least one configured **provider CLI** (e.g. Claude, Codex, Copilot)
  installed and authenticated for non–dry-run workflows, unless you only use
  `dry_run` / `--dry-run`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional dev test dependencies are defined in `pyproject.toml` under
`project.optional-dependencies.dev` (`pytest`, `pytest-cov`, `ruff`).

If your system Python enforces PEP 668 (externally managed environment),
use the virtualenv workflow above.

## Test Commands

```bash
python -m pytest
```

Pytest configuration comes from `pyproject.toml`:

- `testpaths = ["tests"]`
- `addopts = "--tb=short"`

## Running AIDLC Locally

Against this repository:

```bash
aidlc precheck --project .
aidlc run --project .
```

Common targeted commands:

```bash
aidlc run --project . --plan-only
aidlc run --project . --implement-only
aidlc run --project . --resume
aidlc run --project . --skip-validation --skip-finalize
aidlc run --project . --dry-run
aidlc status --project .
```

(Standalone `aidlc audit`, `aidlc finalize`, `aidlc validate`, `aidlc improve`
and `aidlc plan` commands were removed in the core-focus audit.
`finalize` and `validate` engines run inside `aidlc run`. The `audit`
engine remains as a Python module (`aidlc/auditor.py`) but has no current
CLI surface.)

## Packaging

`aidlc` is a Python package with a console entry point:

- script: `aidlc`
- module target: `aidlc.__main__:main`

Build metadata is defined in `pyproject.toml` (`setuptools.build_meta`
backend).

## Repo Layout Notes

- runtime state and generated artifacts are written under `.aidlc/` in the
  **target** repository (the project you're running aidlc against), not in
  the aidlc install location
- bundled planning templates ship as package data from
  `aidlc/project_template/**`

## Lint and format (Ruff)

With dev dependencies installed (`pip install -e ".[dev]"`):

```bash
make lint    # check-only; matches CI and should never modify files
make format  # local fixer; writes formatting changes
```

CI runs `make lint` only (check mode). If CI fails on formatting, run
`make format`, commit those edits, and rerun.

Settings live in `pyproject.toml` under `[tool.ruff]`. There is no
required pre-commit hook in this repository.

## Coverage

CI enforces a line-coverage floor on the `aidlc` package
(`tool.coverage.report.fail_under` in `pyproject.toml`). Run locally:

```bash
python -m pytest --cov=aidlc --cov-report=term-missing -q
```

## pipx (optional)

To install the CLI in an isolated environment while hacking on this repo:

```bash
pipx install --editable '/absolute/path/to/this/repo[dev]'
```

See [deployment.md](deployment.md) for caveats (PATH, multiple `aidlc`
binaries).
