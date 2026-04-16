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

- **Python:** 3.11+
- **Providers:** Configure [Claude CLI](https://docs.anthropic.com/en/docs/claude-cli), Codex, or Copilot per `.aidlc/config.json` (see [docs/configuration.md](docs/configuration.md)). Use `--dry-run` to exercise flows without calling a provider.

## Documentation

All detailed docs live under **[`docs/`](docs/README.md)**:

| Topic | Link |
|--------|------|
| Index | [docs/README.md](docs/README.md) |
| Commands & lifecycle | [docs/cli-lifecycle.md](docs/cli-lifecycle.md) |
| Configuration | [docs/configuration.md](docs/configuration.md) |
| Local dev & tests | [docs/local-development.md](docs/local-development.md) |
| Audit behavior | [docs/audit.md](docs/audit.md) |
| Limits & deprecations | [docs/limitations.md](docs/limitations.md), [docs/deprecations.md](docs/deprecations.md) |

## Deploy / distribute

Install from a git checkout or a built wheel; entry point: `aidlc`. See **[docs/deployment.md](docs/deployment.md)**.
