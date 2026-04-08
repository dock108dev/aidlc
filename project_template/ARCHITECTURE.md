# Architecture

<!-- AIDLC Priority: 0 (loaded first). This file tells the planner HOW the
     system is built so it can generate issues that fit the architecture. -->

## System Overview

<!-- One paragraph + a diagram. Describe the major components and how data
     flows between them. ASCII diagrams work — AIDLC reads text, not images. -->

```
┌─────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐
│  Client  │───▶│   API    │───▶│  Service │───▶│   Data    │
│  (web)   │◀───│  Layer   │◀───│  Layer   │◀───│   Store   │
└─────────┘    └──────────┘    └──────────┘    └───────────┘
                    │                               │
                    ▼                               ▼
              ┌──────────┐                    ┌───────────┐
              │  Auth /  │                    │  Cache /  │
              │  Authz   │                    │  Queue    │
              └──────────┘                    └───────────┘
```

{Describe the flow in 2-3 sentences.}

## Components

<!-- List every major component. AIDLC uses this to understand module
     boundaries when generating implementation issues. -->

### {Component 1: e.g., API Layer}

| Attribute      | Value                                  |
|---------------|----------------------------------------|
| Location      | `src/{path}/`                          |
| Responsibility| {What it does}                         |
| Depends on    | {Other components it calls}            |
| Depended by   | {What calls it}                        |
| Key files     | `{file1}`, `{file2}`                   |

**Interfaces:**
- `{endpoint/function}` — {what it does}
- `{endpoint/function}` — {what it does}

### {Component 2: e.g., Service Layer}

| Attribute      | Value                                  |
|---------------|----------------------------------------|
| Location      | `src/{path}/`                          |
| Responsibility| {What it does}                         |
| Depends on    | {Other components}                     |
| Depended by   | {What uses it}                         |
| Key files     | `{file1}`, `{file2}`                   |

### {Component 3: e.g., Data Store}

| Attribute      | Value                                  |
|---------------|----------------------------------------|
| Location      | `src/{path}/`                          |
| Responsibility| {What it does}                         |
| Technology    | {PostgreSQL / SQLite / Redis / etc.}   |
| Schema        | See `specs/data-model.md`              |

## Data Flow

<!-- Describe the critical paths through the system. AIDLC uses this to
     understand dependencies and generate issues in the right order. -->

### {Path 1: e.g., User Registration}

```
1. Client POST /api/register {email, password}
2. API validates input (schema validation)
3. Service checks for duplicate email
4. Service hashes password (bcrypt, cost=12)
5. Data store inserts user record
6. Service generates welcome email event
7. API returns 201 {user_id, email}
```

### {Path 2: e.g., Data Processing Pipeline}

```
1. {Step 1}
2. {Step 2}
3. {Step 3}
```

## Cross-Cutting Concerns

### Error Handling

{How errors propagate. What format error responses use. Where errors are logged.}

### Authentication / Authorization

{Auth mechanism. Token format. Permission model. Where auth is enforced.}

### Logging & Observability

{Log format. What gets logged. Structured logging fields. Metrics if any.}

### Configuration

{How config is loaded. Environment variables vs config files. Secrets handling.}

## Technology Stack

| Layer            | Technology      | Version  | Why                           |
|-----------------|-----------------|----------|-------------------------------|
| Language        | {Python}        | {3.12}   | {Team expertise}              |
| Web framework   | {FastAPI}       | {0.109}  | {Async, auto-docs}           |
| Database        | {PostgreSQL}    | {16}     | {JSONB, reliability}         |
| Cache           | {Redis}         | {7.x}    | {Session cache, rate limits} |
| Queue           | {Celery}        | {5.x}    | {Async tasks}                |
| Testing         | {pytest}        | {8.x}    | {Standard, fixtures}         |

## Boundaries and Invariants

<!-- Rules the architecture MUST maintain. AIDLC checks implementations
     against these during issue generation. -->

- {e.g., All database access goes through the repository layer — no raw SQL in services}
- {e.g., External API calls are wrapped in circuit breakers}
- {e.g., No business logic in the API layer — only validation and routing}
- {e.g., All async operations are idempotent}
- {e.g., State is never stored in memory across requests}
