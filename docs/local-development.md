# Local Development

## Requirements

- Python 3.11+
- A virtual environment for local development
- Provider CLIs only when running non-dry-run lifecycles

Dev dependencies are defined in `pyproject.toml` under
`project.optional-dependencies.dev`.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

If your system Python enforces PEP 668, use the virtualenv workflow above
instead of installing into the system environment.

## Common Commands

```bash
python -m pytest
python -m pytest --cov=aidlc --cov-report=term-missing -q
make lint
make format
make security
```

`make lint` runs:

- `ruff check aidlc tests`
- `ruff format --check aidlc tests`
- `python -m compileall -q aidlc`

`make security` creates an ephemeral virtualenv, installs `.[dev]`, then runs
`pip-audit` and `bandit` with the settings from `pyproject.toml`.

## Test and Coverage Configuration

Pytest configuration lives in `pyproject.toml`:

- `testpaths = ["tests"]`
- `addopts = "--tb=short"`

Coverage configuration also lives in `pyproject.toml`:

- source package: `aidlc`
- omit: `aidlc/configs/*`
- fail-under: `91`

CI runs coverage on Python 3.12 and regular tests on Python 3.11, 3.12, and
3.13.

## Running the CLI Locally

Against the AIDLC checkout:

```bash
aidlc precheck --project .
aidlc run --project . --dry-run
aidlc status --project .
```

Against another repository:

```bash
aidlc init --project /path/to/target-repo
aidlc run --project /path/to/target-repo
```

The target repository receives `.aidlc/` state and `BRAINDUMP.md`; the AIDLC
checkout does not own that target state.

## Packaging

The package is built with setuptools:

```bash
python -m build
twine check --strict dist/*
```

The console entry point is `aidlc = aidlc.__main__:main`.

Package data includes:

- `aidlc/configs/*.json`
- `aidlc/project_template/**/*.md`

## CI

GitHub Actions workflow `.github/workflows/ci.yml` runs:

- lint and bytecode compile
- dependency audit and Bandit
- dependency review on pull requests
- tests on Python 3.11, 3.12, and 3.13
- coverage upload from Python 3.12
- wheel/sdist build, `twine check`, and smoke install

There is no required pre-commit hook in this repository.
