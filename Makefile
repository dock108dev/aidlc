# Local dev tasks (mirrors CI lint job).
# Install tools: pip install -e ".[dev]"

.PHONY: lint format help

help:
	@echo "Targets:"
	@echo "  make lint    - ruff check + ruff format --check (same as CI)"
	@echo "  make format  - apply ruff formatter to aidlc/ and tests/"

lint:
	ruff check aidlc tests
	ruff format --check aidlc tests

format:
	ruff format aidlc tests
