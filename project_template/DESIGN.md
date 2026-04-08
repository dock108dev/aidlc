# Design Decisions

<!-- AIDLC Priority: 0. This file captures the WHY behind technical choices.
     It prevents AIDLC from second-guessing decisions and ensures generated
     code follows established patterns. Think of this as the "art bible" in
     game development — every asset follows these rules. -->

## Design Principles

<!-- 3-5 principles that guide ALL implementation decisions.
     AIDLC uses these to evaluate trade-offs when generating code. -->

1. **{Principle 1: e.g., Simplicity over cleverness}**
   - {What it means in practice: prefer stdlib over dependencies, flat over nested, explicit over implicit}

2. **{Principle 2: e.g., Test-first development}**
   - {What it means: every feature has tests before implementation, edge cases are tested}

3. **{Principle 3: e.g., Fail fast, fail loud}**
   - {What it means: validate at boundaries, raise exceptions don't return None, log errors with context}

4. **{Principle 4: e.g., Convention over configuration}**
   - {What it means: sensible defaults, minimal config, follow framework idioms}

## Patterns in Use

<!-- Concrete patterns AIDLC should follow when generating code. -->

### {Pattern 1: e.g., Repository Pattern}

**Where:** Data access layer
**Why:** Decouple business logic from database specifics

```{language}
# Example of the pattern as used in this project:

class UserRepository:
    def __init__(self, db: Database):
        self.db = db

    def get_by_id(self, user_id: str) -> User | None:
        row = self.db.query("SELECT * FROM users WHERE id = ?", user_id)
        return User.from_row(row) if row else None

    def save(self, user: User) -> None:
        self.db.execute(
            "INSERT INTO users (id, email, ...) VALUES (?, ?, ...)",
            user.id, user.email, ...
        )
```

**Rules:**
- Every data entity gets its own repository
- Repositories return domain objects, never raw rows
- No SQL outside repository classes

### {Pattern 2: e.g., Service Layer}

**Where:** Business logic
**Why:** Orchestrate operations across multiple repositories

```{language}
# Example:

class UserService:
    def __init__(self, user_repo: UserRepository, email_service: EmailService):
        self.user_repo = user_repo
        self.email_service = email_service

    def register(self, email: str, password: str) -> User:
        if self.user_repo.get_by_email(email):
            raise DuplicateEmailError(email)
        user = User.create(email=email, password=hash_password(password))
        self.user_repo.save(user)
        self.email_service.send_welcome(user.email)
        return user
```

**Rules:**
- Services contain all business logic
- Services are stateless
- Services receive dependencies via constructor

### {Pattern 3: e.g., Error Handling}

**Where:** Everywhere
**Why:** Consistent error responses and debugging

```{language}
# Domain errors — use specific exception classes:
class AppError(Exception):
    def __init__(self, message: str, code: str, status: int = 400):
        self.message = message
        self.code = code
        self.status = status

class NotFoundError(AppError):
    def __init__(self, resource: str, id: str):
        super().__init__(f"{resource} {id} not found", "NOT_FOUND", 404)

# API layer catches and formats:
# {"error": {"code": "NOT_FOUND", "message": "User abc123 not found"}}
```

**Rules:**
- Never catch broad `Exception` in business logic
- Every error type has a unique code string
- All errors include enough context to debug without reproducing

## API Design

<!-- If the project has an API, define the conventions here. -->

### URL Structure

```
{METHOD} /api/v1/{resource}          # Collection
{METHOD} /api/v1/{resource}/{id}     # Individual
{METHOD} /api/v1/{resource}/{id}/{sub-resource}  # Nested
```

### Request/Response Format

```json
// Success response:
{
  "data": { ... },
  "meta": { "request_id": "...", "timestamp": "..." }
}

// Error response:
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable description",
    "details": [ ... ]
  }
}

// Paginated list:
{
  "data": [ ... ],
  "meta": {
    "page": 1,
    "per_page": 20,
    "total": 142,
    "total_pages": 8
  }
}
```

### Status Codes

| Code | When                                   |
|------|----------------------------------------|
| 200  | Success (GET, PUT)                     |
| 201  | Created (POST)                         |
| 204  | No content (DELETE)                    |
| 400  | Validation error                       |
| 401  | Not authenticated                      |
| 403  | Not authorized                         |
| 404  | Resource not found                     |
| 409  | Conflict (duplicate, state conflict)   |
| 422  | Unprocessable (valid JSON, bad data)   |
| 500  | Server error (never intentional)       |

## Data Conventions

### Naming

| Context         | Convention         | Example                      |
|-----------------|-------------------|------------------------------|
| Database tables | snake_case plural | `user_accounts`              |
| Database columns| snake_case        | `created_at`                 |
| API fields      | {snake_case/camelCase} | `{user_id / userId}`    |
| Code variables  | {language convention}  | `{user_account}`        |
| Constants       | UPPER_SNAKE_CASE  | `MAX_RETRY_ATTEMPTS`         |

### IDs

- Format: {UUID v4 / ULID / auto-increment}
- Generated by: {database / application}

### Timestamps

- Format: ISO 8601 with timezone (`2024-01-15T09:30:00Z`)
- Storage: UTC always
- Display: Convert to user's timezone in client

### Soft Deletes

{Yes/No}. If yes: `deleted_at` column, filter in queries, purge policy.

## Testing Strategy

| Layer          | Tool      | What to test                          | Coverage target |
|----------------|-----------|---------------------------------------|-----------------|
| Unit           | {pytest}  | Pure functions, domain logic          | 95%             |
| Integration    | {pytest}  | Database queries, service interactions| 85%             |
| API / E2E      | {pytest}  | Full request/response cycles          | Core paths      |
| Performance    | {locust}  | Response times under load             | P95 < {target}  |

### Test file conventions

```
tests/
├── unit/
│   ├── test_{module}.py        # One test file per source module
│   └── conftest.py             # Shared fixtures
├── integration/
│   ├── test_{feature}.py       # One per feature area
│   └── conftest.py             # DB fixtures, test server
└── conftest.py                 # Top-level fixtures
```

## Anti-Patterns to Avoid

<!-- Explicit "don't do this" list. AIDLC respects these during implementation. -->

- **No god objects** — classes with > 10 public methods need splitting
- **No silent failures** — every `except` block must log or re-raise
- **No circular imports** — dependency direction: API → Service → Repository → Model
- **No magic strings** — use enums or constants for repeated values
- **No raw SQL in services** — always go through repositories
- **No test pollution** — each test must be independent and idempotent
