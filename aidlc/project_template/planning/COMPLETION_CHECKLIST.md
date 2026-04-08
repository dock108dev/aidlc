# Completion Checklist

<!-- AIDLC Priority: 1. This is the "100% completion guide." Every item here
     maps to a verifiable deliverable. AIDLC uses this to ensure nothing is
     missed during planning — like the collectibles tracker in a game. -->

## Core Functionality

<!-- Each section maps to a feature area. Mark items as they're completed.
     AIDLC reads the unchecked items to generate remaining issues. -->

### {Feature Area 1: e.g., User Management}

- [ ] User registration (email + password)
- [ ] Email verification flow
- [ ] Login / logout
- [ ] Password reset via email
- [ ] User profile (view / edit)
- [ ] Account deletion (GDPR compliance)
- [ ] Session management (token refresh)

### {Feature Area 2: e.g., Core Business Logic}

- [ ] {Specific capability 1}
- [ ] {Specific capability 2}
- [ ] {Specific capability 3}
- [ ] {Specific capability 4}

### {Feature Area 3: e.g., API Endpoints}

- [ ] `GET /api/{resource}` — list with pagination
- [ ] `GET /api/{resource}/{id}` — single item
- [ ] `POST /api/{resource}` — create with validation
- [ ] `PUT /api/{resource}/{id}` — update
- [ ] `DELETE /api/{resource}/{id}` — soft delete
- [ ] Input validation on all endpoints
- [ ] Proper error responses (400, 401, 404, 409, 422)
- [ ] Rate limiting on public endpoints

## Quality Gates

### Testing

- [ ] Unit tests for all domain logic (>90% coverage)
- [ ] Integration tests for database operations
- [ ] API tests for all endpoints
- [ ] Edge case tests (empty input, max values, concurrent access)
- [ ] Error path tests (invalid data, missing resources, timeouts)
- [ ] Test fixtures and factories

### Security

- [ ] Input sanitization on all user-facing inputs
- [ ] SQL injection prevention (parameterized queries)
- [ ] XSS prevention (output encoding)
- [ ] CSRF protection on state-changing endpoints
- [ ] Authentication on all non-public endpoints
- [ ] Authorization checks (users can only access their data)
- [ ] Secrets not hardcoded (use environment variables)
- [ ] Dependencies scanned for vulnerabilities

### Performance

- [ ] Database indexes on frequently queried columns
- [ ] N+1 query elimination
- [ ] Connection pooling configured
- [ ] Response caching where appropriate
- [ ] Pagination on all list endpoints

### Operations

- [ ] Health check endpoint
- [ ] Structured logging (JSON format)
- [ ] Error tracking / alerting setup
- [ ] Database migrations versioned
- [ ] Configuration documented
- [ ] Deployment runbook

### Documentation

- [ ] API documentation (OpenAPI / Swagger)
- [ ] README with quick start
- [ ] Architecture decision records
- [ ] Contributing guide
- [ ] Changelog

## Acceptance Criteria Template

<!-- Use this format for every deliverable. AIDLC turns these into
     issue acceptance criteria that drive implementation and verification. -->

```
Feature: {Name}
  Given {precondition}
  When {action}
  Then {observable outcome}
  And {additional verification}
```

**Example:**
```
Feature: User Registration
  Given a new user with a valid email
  When they POST to /api/register with email and password
  Then they receive a 201 response with their user_id
  And a verification email is sent to their address
  And they cannot log in until email is verified
```
