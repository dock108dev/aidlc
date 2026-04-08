# Constraints & Requirements

<!-- AIDLC Priority: 1. Hard constraints that EVERY generated issue must respect.
     These are the "physics engine rules" — they can't be broken, ever. -->

## Technical Constraints

### Language & Runtime

- **Language**: {Python 3.12+ / TypeScript 5.x / Rust 1.75+ / etc.}
- **Runtime**: {CPython / Node.js 20+ / etc.}
- **Minimum supported platforms**: {Linux x86_64, macOS arm64 / etc.}
- **Maximum memory**: {256MB / 512MB / no limit}
- **Maximum startup time**: {< 5s / < 30s / no constraint}

### Dependencies

- **Maximum dependency count**: {e.g., 15 direct dependencies}
- **Banned licenses**: {GPL / AGPL / none}
- **Pinning strategy**: {exact / compatible / major}
- **Security**: All deps must have no known CVEs at merge time

### Performance

| Metric                   | Target          | How to measure              |
|--------------------------|-----------------|------------------------------|
| API response time (P50)  | < {50}ms        | `{tool/command}`             |
| API response time (P95)  | < {200}ms       | `{tool/command}`             |
| Throughput               | > {100} req/s   | `{tool/command}`             |
| Memory per request       | < {10}MB        | `{tool/command}`             |
| Database query time      | < {50}ms        | Query EXPLAIN                |
| Startup time             | < {5}s          | Time from process start      |

### Data

- **Database**: {PostgreSQL 16 / SQLite / DynamoDB / etc.}
- **Maximum record size**: {e.g., 1MB per document}
- **Data retention**: {e.g., 90 days for logs, forever for user data}
- **Backup strategy**: {e.g., daily snapshots, WAL streaming}
- **Character encoding**: UTF-8 everywhere

### Security

- **Authentication**: {JWT / session cookies / API keys}
- **Token lifetime**: {15 min access, 7 day refresh}
- **Password policy**: {min 8 chars, bcrypt cost 12}
- **TLS**: Required in production, optional in dev
- **CORS**: {specific origins / same-origin only}
- **Rate limiting**: {100 req/min per IP on public endpoints}

## Business Constraints

### Compliance

- {e.g., GDPR: users can export and delete their data}
- {e.g., SOC 2: audit logs for all admin actions}
- {e.g., HIPAA: PHI must be encrypted at rest and in transit}

### Availability

- **Target uptime**: {99.9% / 99.95%}
- **Maintenance windows**: {Sundays 2-4 AM UTC / none}
- **Degradation strategy**: {Serve stale cache / show maintenance page / etc.}

### Compatibility

- **API versioning**: {URL prefix /v1 / header / none}
- **Breaking changes**: {Never / semver / with deprecation period}
- **Backward compatibility**: {Last 2 major versions / current only}

## Development Constraints

### Workflow

- **Branch strategy**: {trunk-based / gitflow / feature branches}
- **Review required**: {1 approval / 2 approvals / no review}
- **CI must pass**: {Yes, blocking / Yes, advisory / No CI}
- **Test coverage threshold**: {90% / 80% / none}
- **Merge strategy**: {squash / rebase / merge commit}

### Code Quality

- **Linter**: {ruff / eslint / clippy} — zero warnings allowed
- **Formatter**: {black / prettier / rustfmt} — enforced
- **Type checking**: {mypy strict / TypeScript strict / none}
- **Max function length**: {30 lines / 50 lines / no limit}
- **Max file length**: {500 lines / 1000 lines / no limit}
- **Max cyclomatic complexity**: {10 / 15 / no limit}

## Non-Functional Requirements

| Requirement              | Specification                                |
|--------------------------|----------------------------------------------|
| Logging                  | Structured JSON, {levels}                    |
| Error reporting          | {Sentry / custom / stdout}                   |
| Metrics                  | {Prometheus / StatsD / none}                 |
| Tracing                  | {OpenTelemetry / none}                       |
| Configuration            | {Env vars / config file / both}              |
| Secrets management       | {Env vars / vault / AWS SSM}                 |
| Container support        | {Docker required / optional / N/A}           |
| Offline capability       | {Full / partial / none}                      |
