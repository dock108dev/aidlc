# Deployment & distribution

AIDLC is a normal Python package (setuptools). There is no separate server process: you install the CLI and run it against a **project directory** that contains or will receive `.aidlc/`.

## Install from a checkout

```bash
pip install -e .
# optional dev deps (pytest, ruff, etc.)
pip install -e ".[dev]"
```

Entry point: `aidlc` → `aidlc.__main__:main`.

## Install with pipx (isolated CLI)

From a local clone (editable, with dev tools in the pipx venv):

```bash
pipx install --editable '/absolute/path/to/aidlc[dev]'
```

Use an absolute path. Avoid running `pipx uninstall aidlc` from a directory whose name is `aidlc`, or pipx may treat the name as a path—run the uninstall from `$HOME` or another directory.

After install, ensure `~/.local/bin` (or your pipx apps path) precedes other `aidlc` shims on `PATH` if multiple copies exist.

## Install from a wheel

```bash
python -m build
pip install dist/aidlc-*.whl
```

## CI / automation

- Use a virtualenv or `pip install --user` as appropriate for your runner.
- Run `aidlc run --project <repo>` from the repo root (or pass `--project`).
- Non-interactive hosts should set provider auth in the environment your CLI expects (e.g. Codex login, Copilot token docs) or use `--dry-run` for smoke tests.

## Configuration

Runtime config is read from **the target project’s** `.aidlc/config.json`, not from the AIDLC install location. See [configuration.md](configuration.md).
