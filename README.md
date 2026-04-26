# AIDLC

Python CLI for an **AI-assisted development lifecycle** inside a target
repository.

The flow is intentionally narrow:

1. The customer writes `BRAINDUMP.md` — what to build, what matters.
2. `aidlc run` scans the repo, plans work as issues, implements them with
   provider-backed agents, validates, and reports.

For new repos, the BRAINDUMP describes the product. For existing repos, it
describes what to add or change next. Either way, AIDLC works from the
braindump plus whatever supporting docs are present.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

aidlc init --project /path/to/target-repo   # scaffolds .aidlc/ + BRAINDUMP.md
# edit BRAINDUMP.md to describe what you want built
aidlc run  --project /path/to/target-repo
```

Isolated CLI install (optional):

```bash
pipx install --editable '/path/to/aidlc/checkout[dev]'
```

- **Python:** 3.11+
- **Providers:** Configure Claude CLI, OpenAI Codex CLI, GitHub Copilot CLI, etc. per the **target repo’s** `.aidlc/config.json` (see [docs/configuration.md](docs/configuration.md)). Use `dry_run: true` or `--dry-run` to exercise flows without calling a provider.

## Lint and format

```bash
make lint    # check-only: ruff lint + ruff format --check (same as CI)
make format  # write formatting changes to aidlc/ and tests/
```

## Documentation

All guides and reference material (architecture, lifecycle, config, deployment, migration) are in **[`docs/`](docs/README.md)**. Start at the **[documentation index](docs/README.md)**.

## Deploy / distribute

Install from a git checkout or a built wheel; entry point `aidlc`. See **[docs/deployment.md](docs/deployment.md)**.
