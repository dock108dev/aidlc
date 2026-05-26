# AIDLC

AIDLC is a Python CLI for running an AI-assisted development lifecycle inside a
target repository.

The workflow is intentionally narrow:

1. Write `BRAINDUMP.md` at the target repository root.
2. Run `aidlc run`.
3. AIDLC scans the repo, plans work as issues, implements, validates,
   finalizes, and writes run reports under `.aidlc/`.

## Run Locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

aidlc init --project /path/to/target-repo
# edit /path/to/target-repo/BRAINDUMP.md
aidlc run --project /path/to/target-repo
```

Useful local checks:

```bash
make lint
python -m pytest -q
make security
```

## Runtime Basics

- Python 3.11+ is required.
- The installed command is `aidlc`.
- Runtime state is written to the target repository's `.aidlc/` directory.
- Default provider config enables Codex/OpenAI and disables Claude/Copilot.
- Non-dry-run work requires the relevant provider CLI to be installed and
  authenticated.
- `aidlc run --dry-run` exercises the lifecycle without provider calls.

## Distribution

AIDLC can run from an editable checkout, pipx install, or built wheel. There is
no server process to deploy.

See [docs/deployment.md](docs/deployment.md) for install and automation notes.

## Documentation

Detailed docs live in [docs/](docs/README.md):

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [cli-lifecycle.md](docs/cli-lifecycle.md)
- [configuration.md](docs/configuration.md)
- [local-development.md](docs/local-development.md)
- [deployment.md](docs/deployment.md)
- [known-limitations.md](docs/known-limitations.md)
- [deprecations.md](docs/deprecations.md)
