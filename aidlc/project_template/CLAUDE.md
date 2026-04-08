# CLAUDE.md — AI Development Instructions

<!-- AIDLC Priority: 0. This file speaks directly to Claude during both
     planning and implementation. It's the "modder's guide" — tells the AI
     exactly how to work with this codebase. -->

## Project Identity

- **Name**: {PROJECT_NAME}
- **Language**: {Python 3.12 / TypeScript 5.x / etc.}
- **Package manager**: {pip / npm / cargo / etc.}
- **Test command**: `{python -m pytest / npm test / cargo test}`
- **Lint command**: `{ruff check . / eslint . / clippy}`
- **Build command**: `{pip install -e . / npm run build / cargo build}`

## Coding Standards

### Style

- Follow {PEP 8 / Airbnb style guide / Rust fmt} strictly
- Max line length: {88 / 100 / 120} characters
- Imports: {sorted by stdlib, third-party, local / use absolute imports}
- Docstrings: {Google style / NumPy style / JSDoc}
- Type hints: {required on all public functions / optional}

### File Organization

```
# Every source file follows this structure:
1. Module docstring (one sentence)
2. Imports (stdlib, third-party, local — separated by blank lines)
3. Constants
4. Type definitions / dataclasses
5. Main classes / functions
6. Private helpers (prefix with _)
```

### Naming Conventions

| What                | Convention          | Example                    |
|--------------------|---------------------|----------------------------|
| Files              | {snake_case}        | `user_service.py`          |
| Classes            | {PascalCase}        | `UserService`              |
| Functions/methods  | {snake_case}        | `get_user_by_id`           |
| Constants          | {UPPER_SNAKE_CASE}  | `MAX_RETRIES`              |
| Type parameters    | {Single uppercase}  | `T`, `K`, `V`              |
| Test functions     | {test_description}  | `test_login_with_invalid_email_returns_401` |

## Implementation Rules

<!-- These are absolute rules Claude must follow. Be specific. -->

### DO

- Write tests BEFORE or alongside implementation (never after)
- Use dependency injection — never instantiate dependencies inside classes
- Handle errors at boundaries — validate inputs, wrap external calls
- Keep functions under 30 lines — extract helpers for complex logic
- Use {language-specific idioms: e.g., list comprehensions, pattern matching}
- Log at appropriate levels: DEBUG for internals, INFO for operations, ERROR for failures
- Return early — avoid deep nesting, prefer guard clauses

### DO NOT

- Do NOT add comments that restate the code — only explain "why"
- Do NOT use `print()` — use the logging framework
- Do NOT catch broad exceptions (`except Exception`) in business logic
- Do NOT use mutable default arguments
- Do NOT import from `__init__.py` internals — use public API
- Do NOT add dependencies without justification in the issue
- Do NOT modify files unrelated to the current issue
- Do NOT leave TODO/FIXME comments — create issues instead

## Testing Rules

### Unit Tests

```{language}
# Every test follows AAA pattern: Arrange, Act, Assert
# File: tests/unit/test_{module}.py

def test_{what_it_does}_{scenario}_{expected_result}():
    # Arrange
    {setup}

    # Act
    result = {call}

    # Assert
    assert result == {expected}
```

### What to Test

- Happy path (primary use case)
- Edge cases (empty input, max values, boundary conditions)
- Error cases (invalid input, missing data, network failures)
- State transitions (before/after, side effects)

### What NOT to Test

- Third-party library internals
- Trivial getters/setters
- Framework boilerplate (routing declarations, middleware config)

## Dependencies

### Approved Dependencies

<!-- Only these are allowed. Adding new ones requires justification. -->

| Package              | Purpose                | Version    |
|---------------------|------------------------|------------|
| {e.g., fastapi}     | Web framework          | {^0.109}   |
| {e.g., sqlalchemy}  | Database ORM           | {^2.0}     |
| {e.g., pydantic}    | Data validation        | {^2.5}     |
| {e.g., httpx}       | HTTP client            | {^0.27}    |

### Banned Dependencies

- {e.g., requests} — use httpx instead (async support)
- {e.g., flask} — we use fastapi
- {e.g., pymongo} — we use PostgreSQL

## Git Conventions

- Branch from `main` for all work
- Commit messages: `{type}: {description}` where type is feat/fix/refactor/test/docs
- One logical change per commit
- Never commit generated files, secrets, or `.env`

## Environment

### Required Environment Variables

```bash
# {e.g., DATABASE_URL=postgresql://user:pass@localhost:5432/dbname}
# {e.g., SECRET_KEY=<random-64-chars>}
# {e.g., REDIS_URL=redis://localhost:6379/0}
```

### Development Setup

```bash
{Step-by-step commands to get from clone to running tests}
```
