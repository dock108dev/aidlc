# Error Handling Design

<!-- AIDLC Priority: 1. Consistent error handling across the entire codebase.
     AIDLC uses this to generate proper error handling in every issue. -->

## Error Taxonomy

### Application Errors (4xx — client caused)

| Code                  | HTTP | When to use                               | User-facing message template            |
|-----------------------|------|-------------------------------------------|-----------------------------------------|
| `VALIDATION_ERROR`    | 400  | Request body fails schema validation      | "Invalid input: {details}"             |
| `UNAUTHORIZED`        | 401  | No auth token or token expired            | "Authentication required"               |
| `FORBIDDEN`           | 403  | Valid auth but insufficient permissions    | "You don't have permission to {action}" |
| `NOT_FOUND`           | 404  | Resource doesn't exist                    | "{resource} not found"                  |
| `CONFLICT`            | 409  | Duplicate or state conflict               | "{resource} already exists"             |
| `UNPROCESSABLE`       | 422  | Valid syntax but business rule violation   | "{specific reason}"                     |
| `RATE_LIMITED`        | 429  | Too many requests                         | "Rate limit exceeded, retry after {n}s" |

### System Errors (5xx — our fault)

| Code                  | HTTP | When to use                               | Action                                  |
|-----------------------|------|-------------------------------------------|-----------------------------------------|
| `INTERNAL_ERROR`      | 500  | Unhandled exception                       | Log full stack trace, alert team        |
| `SERVICE_UNAVAILABLE` | 503  | Database/external service down            | Log, retry if transient                 |
| `TIMEOUT`             | 504  | External call exceeded timeout            | Log, return degraded response if possible|

## Error Response Format

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid input: email format is incorrect",
    "details": [
      {
        "field": "email",
        "constraint": "format",
        "message": "Must be a valid email address"
      }
    ],
    "request_id": "req_abc123"
  }
}
```

## Implementation Pattern

```{language}
# Base error class — all application errors extend this
class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: list = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or []
        super().__init__(message)

# Specific error classes
class ValidationError(AppError):
    def __init__(self, message: str, details: list = None):
        super().__init__("VALIDATION_ERROR", message, 400, details)

class NotFoundError(AppError):
    def __init__(self, resource: str, identifier: str):
        super().__init__("NOT_FOUND", f"{resource} '{identifier}' not found", 404)

class ConflictError(AppError):
    def __init__(self, resource: str, reason: str):
        super().__init__("CONFLICT", f"{resource}: {reason}", 409)

# Usage in service layer:
def get_user(user_id: str) -> User:
    user = user_repo.get_by_id(user_id)
    if not user:
        raise NotFoundError("User", user_id)
    return user

# API layer handler catches and formats:
@app.exception_handler(AppError)
def handle_app_error(request, exc: AppError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}}
    )
```

## Logging Rules

| Level   | When                                          | Example                                    |
|---------|-----------------------------------------------|--------------------------------------------|
| DEBUG   | Internal state for troubleshooting            | "Cache miss for key user:123"             |
| INFO    | Normal operations, state changes              | "User abc registered successfully"         |
| WARNING | Recoverable issues, deprecation usage         | "Retry 2/3 for external API call"         |
| ERROR   | Failures requiring investigation              | "Database connection failed: timeout"      |

**Every log entry MUST include:**
- Timestamp (ISO 8601)
- Level
- Message
- Context fields: `request_id`, `user_id` (if applicable), `operation`

**Never log:**
- Passwords, tokens, or secrets
- Full request/response bodies (log summaries instead)
- PII beyond what's needed for debugging
