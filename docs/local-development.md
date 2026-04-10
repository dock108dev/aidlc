# Local Development

## Prerequisites

- Python 3.11+
- Claude CLI installed and authenticated for non-dry-run workflows

## Setup

```bash
pip install -e .
```

Optional dev test dependencies are defined in `pyproject.toml` under `project.optional-dependencies.dev`.

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
