# {PROJECT_NAME}

> One sentence: what this project does and who it's for.

## Status

| Attribute    | Value                                          |
|-------------|------------------------------------------------|
| Stage       | {pre-alpha / alpha / beta / stable}            |
| Version     | {0.1.0}                                        |
| Language    | {Python 3.12 / TypeScript 5.x / Rust / etc.}  |
| Framework   | {FastAPI / Next.js / Axum / etc.}              |
| License     | {MIT / Apache-2.0 / proprietary}               |
| Test Suite  | {pytest / jest / cargo test / go test}          |
| CI          | {GitHub Actions / none}                        |

## What This Project Does

<!-- 3-5 sentences. Describe the problem it solves, not just what it is.
     AIDLC uses this to understand scope and generate relevant issues. -->

{PROJECT_NAME} solves {PROBLEM} for {AUDIENCE} by {APPROACH}.

Current capabilities:
- {Capability 1}
- {Capability 2}
- {Capability 3}

## What This Project Does NOT Do

<!-- Explicit scope boundaries prevent AIDLC from generating out-of-scope issues. -->

- Does NOT {thing people might assume}
- Does NOT {another boundary}
- Will NOT support {explicit exclusion} in this version

## Quick Start

```bash
# Install
{install command}

# Run
{run command}

# Test
{test command}
```

## Project Structure

```
{PROJECT_NAME}/
├── src/                    # Source code
│   ├── {module_a}/         # {What module_a does}
│   ├── {module_b}/         # {What module_b does}
│   └── main.{ext}         # Entry point
├── tests/                  # Test suite
├── docs/                   # Extended documentation
├── planning/               # Planning and roadmap docs
├── specs/                  # Feature specifications
├── design/                 # Architecture and design docs
├── .aidlc/                 # AIDLC working directory
│   ├── config.json         # AIDLC configuration
│   └── issues/             # Generated issues
└── {config files}          # pyproject.toml, package.json, etc.
```

## Key Decisions

<!-- These are the "why" behind the codebase. They prevent AIDLC from
     suggesting alternatives to decisions you've already made. -->

| Decision                         | Choice        | Rationale                                    |
|----------------------------------|---------------|----------------------------------------------|
| {e.g., Database}                 | {PostgreSQL}  | {Need JSONB + full-text search}              |
| {e.g., Auth}                     | {JWT}         | {Stateless, works with our CDN}              |
| {e.g., State management}         | {Redux}       | {Team familiarity, devtools}                 |
| {e.g., Deployment}               | {Docker + K8s}| {Already have cluster, need auto-scaling}    |

## Non-Negotiable Constraints

<!-- Hard constraints AIDLC must never violate. -->

- {e.g., Must run on Python 3.11+ (no 3.10 compatibility)}
- {e.g., All API responses must follow JSON:API spec}
- {e.g., Zero external network calls during tests}
- {e.g., Must work offline after initial setup}
- {e.g., Maximum 200ms response time for API endpoints}

## See Also

- [ARCHITECTURE.md](ARCHITECTURE.md) — System architecture and data flow
- [ROADMAP.md](ROADMAP.md) — Phased delivery plan with milestones
- [DESIGN.md](DESIGN.md) — Detailed design decisions and patterns
- [CLAUDE.md](CLAUDE.md) — AI-specific instructions and coding standards
