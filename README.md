# AIDLC

Python CLI for an **AI-assisted development lifecycle** inside a target repository: scan documentation, plan work as issues, implement with provider-backed agents, verify, and report.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

aidlc init --project /path/to/target-repo
aidlc run --project /path/to/target-repo
```

Isolated CLI install (optional):

```bash
pipx install --editable '/path/to/aidlc/checkout[dev]'
```

- **Python:** 3.11+
- **Providers:** Configure Claude CLI, OpenAI Codex CLI, GitHub Copilot CLI, etc. per the **target repo’s** `.aidlc/config.json` (see [docs/configuration.md](docs/configuration.md)). Use `dry_run: true` or `--dry-run` to exercise flows without calling a provider.

## Documentation

All guides and reference material (commands, config, auditing, coverage, deployment) are in **`[docs/](docs/README.md)`**. Start at the **[documentation index](docs/README.md)**.

## Deploy / distribute

Install from a git checkout or a built wheel; entry point `aidlc`. See **[docs/deployment.md](docs/deployment.md)**.
