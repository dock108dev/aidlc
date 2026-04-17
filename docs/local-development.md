# Local Development

## Prerequisites

- Python 3.11+
- At least one configured **provider CLI** (e.g. Claude, Codex, Copilot) installed and authenticated for non–dry-run workflows, unless you only use `dry_run` / `--dry-run`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Optional dev test dependencies are defined in `pyproject.toml` under `project.optional-dependencies.dev`.

If your system Python enforces PEP 668 (externally managed environment), use the virtualenv workflow above.

## Test Commands

```bash
python -m pytest
```

Pytest configuration currently comes from `pyproject.toml`:

- `testpaths = ["tests"]`
- `addopts = "--tb=short"`

## Running AIDLC Locally

Against this repository:

```bash
aidlc precheck --project .
aidlc run --project .
```

For full audit testing:

```bash
aidlc audit --project . --full
```

Common targeted commands:

```bash
aidlc audit --project .
aidlc run --project . --plan-only
aidlc run --project . --resume
aidlc finalize --project .
aidlc status --project .
```

## Packaging

`aidlc` is a Python package with console entry point:

- script: `aidlc`
- module target: `aidlc.__main__:main`

Build metadata is defined in `pyproject.toml` (`setuptools.build_meta` backend).

## Repo Layout Notes

- runtime state and generated artifacts are written under `.aidlc/` in the target repository
- bundled planning templates are shipped as package data from `aidlc/project_template/**`

## Lint and format (Ruff)

With dev dependencies installed (`pip install -e ".[dev]"`):

```bash
ruff check aidlc tests
ruff format --check aidlc tests
```

CI runs both. Settings live in `pyproject.toml` under `[tool.ruff]`. There is no required pre-commit hook in this repository.

## Coverage

CI enforces a line-coverage floor on the `aidlc` package (see [COVERAGE_PLAN.md](COVERAGE_PLAN.md) and `pyproject.toml`). Run locally:

```bash
python -m pytest --cov=aidlc --cov-report=term-missing -q
```

## pipx (optional)

To install the CLI in an isolated environment while hacking on this repo:

```bash
pipx install --editable '/absolute/path/to/this/repo[dev]'
```

See [deployment.md](deployment.md) for caveats (PATH, multiple `aidlc` binaries).
