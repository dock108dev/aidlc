# Roadmap

<!-- AIDLC Priority: 0. This is the most important file for planning.
     AIDLC reads this to understand WHAT to build and in WHAT ORDER.
     Think of this as the "level progression" in a game — each phase
     unlocks the next, and every deliverable has clear acceptance criteria. -->

## Delivery Phases

<!-- AIDLC generates issues phase by phase. Earlier phases get higher priority.
     Each phase should be independently shippable. -->

### Phase 1: {Foundation} — {Target: Week 1-2}

**Goal:** {One sentence — what "done" looks like for this phase.}

**Why this first:** {What it unblocks. Why nothing else can start without this.}

**Deliverables:**

1. **{Deliverable 1.1: e.g., Project skeleton}**
   - {What}: Set up repo structure, CI pipeline, linting, test harness
   - {Acceptance criteria}:
     - [ ] `{test command}` runs and passes with 0 tests
     - [ ] CI pipeline runs on every push to main
     - [ ] Linter configured and passing
   - {Files/modules affected}: `{paths}`

2. **{Deliverable 1.2: e.g., Data model}**
   - {What}: Define core database schema and migrations
   - {Acceptance criteria}:
     - [ ] Schema defined in `{path}`
     - [ ] Migrations run cleanly on empty database
     - [ ] Seed data script works
   - {Dependencies}: Deliverable 1.1

3. **{Deliverable 1.3: e.g., Core domain logic}**
   - {What}: Implement the central business rules without I/O
   - {Acceptance criteria}:
     - [ ] All domain functions have unit tests
     - [ ] No external dependencies (pure functions)
     - [ ] Edge cases documented and tested
   - {Dependencies}: Deliverable 1.2

**Phase 1 Exit Criteria:**
- [ ] All deliverables complete
- [ ] Test suite passes
- [ ] {Specific integration test or demo scenario}

---

### Phase 2: {Core Features} — {Target: Week 3-5}

**Goal:** {What "done" looks like.}

**Why after Phase 1:** {What Phase 1 unblocked.}

**Deliverables:**

4. **{Deliverable 2.1: e.g., User authentication}**
   - {What}: Registration, login, logout, session management
   - {Acceptance criteria}:
     - [ ] User can register with email/password
     - [ ] User can log in and receive a token
     - [ ] Invalid credentials return 401
     - [ ] Token expires after {duration}
   - {Dependencies}: Deliverable 1.2 (data model)

5. **{Deliverable 2.2: e.g., Primary API endpoints}**
   - {What}: CRUD operations for core resources
   - {Acceptance criteria}:
     - [ ] `GET /api/{resource}` returns paginated list
     - [ ] `POST /api/{resource}` creates with validation
     - [ ] `PUT /api/{resource}/{id}` updates
     - [ ] `DELETE /api/{resource}/{id}` soft-deletes
     - [ ] All endpoints return proper status codes
     - [ ] Input validation rejects malformed data
   - {Dependencies}: Deliverables 2.1, 1.3

6. **{Deliverable 2.3: e.g., Background processing}**
   - {What}: Async task queue for heavy operations
   - {Acceptance criteria}:
     - [ ] Tasks enqueue and execute within {timeout}
     - [ ] Failed tasks retry {n} times with backoff
     - [ ] Task status queryable via API
   - {Dependencies}: Deliverable 1.1

**Phase 2 Exit Criteria:**
- [ ] All endpoints documented and tested
- [ ] Integration tests pass end-to-end
- [ ] {Specific user journey works}

---

### Phase 3: {Polish & Hardening} — {Target: Week 6-7}

**Goal:** {What "done" looks like.}

**Deliverables:**

7. **{Deliverable 3.1: e.g., Error handling & resilience}**
   - {What}: Circuit breakers, retries, graceful degradation
   - {Acceptance criteria}:
     - [ ] External service failures don't crash the app
     - [ ] Error responses follow consistent format
     - [ ] Rate limiting enforced on public endpoints

8. **{Deliverable 3.2: e.g., Performance optimization}**
   - {What}: Caching, query optimization, connection pooling
   - {Acceptance criteria}:
     - [ ] P95 response time under {target}ms
     - [ ] Database queries use indexes (no full scans)
     - [ ] Cache hit rate above {target}%

9. **{Deliverable 3.3: e.g., Documentation & deployment}**
   - {What}: API docs, deployment runbook, monitoring setup
   - {Acceptance criteria}:
     - [ ] OpenAPI spec generated and accessible
     - [ ] Deployment script works in staging
     - [ ] Health check endpoint returns system status

**Phase 3 Exit Criteria:**
- [ ] Load test passes at {target} RPS
- [ ] All acceptance criteria met across all phases
- [ ] Ready for production deployment

## Dependency Graph

<!-- Visual overview of what blocks what. AIDLC uses this to set
     issue dependencies correctly. -->

```
Phase 1:  [1.1 Skeleton] ──▶ [1.2 Data Model] ──▶ [1.3 Domain Logic]
                                     │
Phase 2:              [2.1 Auth] ────┤
                          │          │
                     [2.2 API] ◀─────┘
                          │
                     [2.3 Tasks]

Phase 3:  [3.1 Resilience]  [3.2 Performance]  [3.3 Docs]
              (all depend on Phase 2 completion)
```

## Out of Scope (This Version)

<!-- Explicit exclusions prevent AIDLC from scope-creeping. -->

- {Feature X} — planned for v2
- {Feature Y} — not needed for MVP
- {Integration Z} — waiting on external team

## Risk Register

<!-- Known risks help AIDLC prioritize defensive issues. -->

| Risk                              | Impact | Mitigation                          |
|-----------------------------------|--------|-------------------------------------|
| {e.g., Third-party API changes}   | High   | {Abstract behind adapter pattern}   |
| {e.g., Data migration complexity} | Medium | {Incremental migration strategy}    |
| {e.g., Performance at scale}      | Medium | {Load test early in Phase 2}        |
