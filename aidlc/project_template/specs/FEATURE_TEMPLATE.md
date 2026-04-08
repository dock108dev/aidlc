# Feature Spec: {Feature Name}

<!-- AIDLC Priority: 1. One file per major feature. This is the "quest design doc"
     — every detail is here so Claude doesn't have to guess.

     Copy this template for each feature. Name the file after the feature:
     specs/user-authentication.md, specs/payment-processing.md, etc. -->

## Overview

| Attribute     | Value                                    |
|--------------|------------------------------------------|
| Feature      | {Feature Name}                           |
| Phase        | {Phase number from ROADMAP.md}           |
| Priority     | {high / medium / low}                    |
| Depends on   | {Other features or infrastructure}       |
| Estimated issues | {3-8 — how many AIDLC issues this maps to} |

## Problem Statement

{2-3 sentences: what problem does this feature solve? Why does it matter?}

## User Stories

<!-- Each story maps to 1-2 AIDLC issues. Use the standard format. -->

### Story 1: {As a {role}, I want to {action}, so that {benefit}}

**Happy path:**
1. User {does X}
2. System {responds with Y}
3. User sees {Z}

**Error cases:**
- If {bad input}: show {error message}
- If {system failure}: {fallback behavior}
- If {unauthorized}: return 401, redirect to login

**Acceptance criteria:**
- [ ] {Specific, testable criterion}
- [ ] {Another criterion}
- [ ] {Edge case handled}

### Story 2: {As a {role}, I want to {action}, so that {benefit}}

**Happy path:**
1. {Step 1}
2. {Step 2}

**Error cases:**
- {Error case 1}
- {Error case 2}

**Acceptance criteria:**
- [ ] {Criterion}
- [ ] {Criterion}

## Data Model

<!-- Tables, schemas, or data structures this feature introduces or modifies. -->

### {Entity Name}

| Field          | Type        | Constraints            | Description            |
|----------------|-------------|------------------------|------------------------|
| `id`           | {UUID}      | PK                     | Unique identifier      |
| `{field_1}`    | {string}    | NOT NULL, max 255      | {What it stores}       |
| `{field_2}`    | {integer}   | >= 0                   | {What it stores}       |
| `{field_3}`    | {timestamp} | NOT NULL, default now  | {What it stores}       |
| `{field_4}`    | {enum}      | {value1, value2}       | {What it stores}       |
| `created_at`   | {timestamp} | NOT NULL               | Record creation time   |
| `updated_at`   | {timestamp} | NOT NULL               | Last modification      |

**Indexes:**
- `idx_{table}_{field}` on `{field}` — {why: common lookup}
- `idx_{table}_{field1}_{field2}` on `({field1}, {field2})` — {why: composite query}

**Relationships:**
- {Entity} belongs to {OtherEntity} via `{foreign_key}`
- {Entity} has many {OtherEntity}s

## API Endpoints

<!-- Only include if this feature introduces or modifies API endpoints. -->

### `{METHOD} /api/v1/{path}`

**Purpose:** {What this endpoint does}

**Request:**
```json
{
  "{field}": "{type} — {description}",
  "{field}": "{type} — {description}"
}
```

**Response (success):**
```json
{
  "data": {
    "{field}": "{type}",
    "{field}": "{type}"
  }
}
```

**Response (error):**
```json
{
  "error": {
    "code": "{ERROR_CODE}",
    "message": "{Human-readable message}"
  }
}
```

**Status codes:** {200 / 201 / 400 / 401 / 404 / 409}

**Auth required:** {Yes / No}

**Rate limit:** {100/min / unlimited}

## Business Rules

<!-- Rules that MUST be enforced in code. These become acceptance criteria. -->

1. {e.g., A user cannot have more than 5 active sessions}
2. {e.g., Passwords must be at least 8 characters with 1 number}
3. {e.g., Deleted records are soft-deleted and purged after 30 days}
4. {e.g., Email addresses are case-insensitive and trimmed}

## Edge Cases

<!-- The "what could go wrong" list. Each becomes a test case. -->

| Scenario                         | Expected behavior                    |
|----------------------------------|--------------------------------------|
| {e.g., Duplicate email signup}   | Return 409 with clear message        |
| {e.g., Expired token}           | Return 401, client refreshes token   |
| {e.g., Empty string input}      | Return 400, field is required        |
| {e.g., Unicode in name field}   | Accept and store correctly (UTF-8)   |
| {e.g., Concurrent updates}      | Last write wins / optimistic locking |
| {e.g., Database timeout}        | Retry once, then return 503          |

## Implementation Notes

<!-- Hints for Claude about HOW to implement, referencing existing code. -->

- Similar to: `{existing_file.py}` — follow that pattern
- Use {library/module} for {specific capability}
- Put new code in `src/{path}/`
- Tests go in `tests/{path}/`
- Migration file: `migrations/{number}_{name}.sql`
