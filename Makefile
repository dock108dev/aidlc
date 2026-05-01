# Local dev tasks (mirrors CI lint job).
# Install tools: pip install -e ".[dev]"

.PHONY: lint format security help

help:
	@echo "Targets:"
	@echo "  make lint      - ruff check + ruff format --check (same as CI)"
	@echo "  make format    - apply ruff formatter to aidlc/ and tests/"
	@echo "  make security  - pip-audit + bandit (same as CI security job)"

lint:
	ruff check aidlc tests
	ruff format --check aidlc tests
	python -m compileall -q aidlc

# Ephemeral venv so pip-audit matches CI (audits only this project + dev deps, not a global conda base).
security:
	@set -e; _pv=$$(mktemp -d); trap 'rm -rf "$$_pv"' EXIT; \
	  python -m venv "$$_pv/v"; \
	  "$$_pv/v/bin/pip" install -q -U pip "setuptools>=78.1.1" wheel; \
	  "$$_pv/v/bin/pip" install -q -e ".[dev]"; \
	  "$$_pv/v/bin/pip-audit" --desc on; \
	  "$$_pv/v/bin/bandit" -c pyproject.toml -r aidlc -q --severity-level medium --confidence-level medium

format:
	ruff format aidlc tests
