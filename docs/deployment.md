# Deployment and Distribution

AIDLC is distributed as a Python CLI. There is no server to deploy and no
background process to keep alive. A production host needs Python, the AIDLC
package, git access to the target repository, and authenticated provider CLIs
for any non-dry-run work.

## Install from a Checkout

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development tools:

```bash
pip install -e ".[dev]"
```

## Install with pipx

```bash
pipx install --editable '/absolute/path/to/aidlc[dev]'
```

Use an absolute path. If multiple `aidlc` commands exist on `PATH`, verify the
one being used:

```bash
type -a aidlc
aidlc --version
```

## Install from a Wheel

```bash
python -m build
pip install dist/aidlc-*.whl
```

The build job in CI validates distributions with `twine check --strict` and
smoke-installs the wheel.

## Running in Automation

From a runner or CI host:

```bash
aidlc run --project /path/to/target-repo
```

Operational requirements:

- The target repo must have or receive `BRAINDUMP.md`.
- The target repo must allow writes to `.aidlc/`.
- Provider CLIs must already be installed and authenticated for non-dry-run
  runs.
- Runtime config is read from the target repo's `.aidlc/config.json` unless
  `--config` is passed.

Use `--dry-run` for smoke tests that should not call providers.

## What Is Not Deployed

AIDLC does not ship:

- web routes
- database migrations
- cron jobs
- workers
- hosted APIs

Autosync is a git commit/push helper that can run during implementation; it is
not a deployment pipeline.
