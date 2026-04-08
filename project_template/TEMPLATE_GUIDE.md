# AIDLC Project Template Guide

## The Video Game Analogy

Building software with AIDLC is like developing a video game. The more detailed
your design documents are before production starts, the closer you get to 100%
completion without improvisation.

```
┌──────────────────────────────────────────────────────────────────┐
│                    DOCUMENT COMPLETENESS                         │
│                                                                  │
│  None ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░ Full            │
│       ▲                                        ▲                │
│       │                                        │                │
│  Claude guesses everything.           Claude executes your       │
│  Random results.                      exact vision.              │
│  Scope creep.                         Deterministic output.      │
│  Wrong patterns.                      Correct patterns.          │
│  Missing features.                    100% completion.           │
└──────────────────────────────────────────────────────────────────┘
```

**Every hour spent on documentation saves 3-5 hours of implementation rework.**

## How AIDLC Reads Your Docs

AIDLC's scanner loads documents in a strict priority order and feeds them
into Claude's context window. Understanding this system lets you put the
right information in the right place.

### Priority System

```
PRIORITY 0 — Loaded FIRST, always included (root-level files)
    ├── README.md          Your project's identity card
    ├── ARCHITECTURE.md    How the system is built
    ├── ROADMAP.md         What to build and in what order ← MOST IMPORTANT
    ├── DESIGN.md          Why decisions were made
    └── CLAUDE.md          Direct instructions for Claude

PRIORITY 1 — Loaded SECOND (special directories)
    ├── planning/          Checklists, constraints, milestones
    ├── specs/             Feature specifications (one per feature)
    ├── design/            Patterns, error handling, testing strategy
    └── rfcs/              Request for comments / proposals

PRIORITY 2 — Loaded THIRD (docs directory)
    └── docs/              API reference, glossary, guides

PRIORITY 3 — Loaded LAST, may be dropped if context is full
    └── {everything else}  Other .md/.txt/.rst files
```

### Context Budget

| Phase          | Budget      | What fits                                    |
|----------------|-------------|----------------------------------------------|
| Planning       | 80,000 chars| ~20-30 well-written docs                     |
| Implementation | 30,000 chars| Project context + issue spec + instructions  |

**Key insight:** Priority 0 files are ALWAYS included. Priority 3 files
might get dropped. Put critical information in Priority 0-1 files.

### Per-Document Limit

Each document is capped at 10,000 characters (configurable via `max_doc_chars`).
Keep individual files focused and under this limit. If a doc is longer, it gets
truncated with `... (truncated)`.

## Template Files

### Priority 0 (The Five Pillars)

| File              | Purpose                           | AIDLC Uses It For                |
|-------------------|-----------------------------------|----------------------------------|
| `README.md`       | Project identity + scope          | Understanding what to build      |
| `ARCHITECTURE.md` | System structure + data flow      | Generating code that fits        |
| `ROADMAP.md`      | Phased delivery plan              | Issue ordering + dependencies    |
| `DESIGN.md`       | Patterns + conventions            | Code style + architecture        |
| `CLAUDE.md`       | Direct AI instructions            | Coding standards + constraints   |

**README.md** answers: *What is this? What does it do? What doesn't it do?*
- The "What This Project Does NOT Do" section is crucial — it prevents scope creep
- The "Key Decisions" table stops Claude from suggesting alternatives
- The "Non-Negotiable Constraints" list sets hard boundaries

**ARCHITECTURE.md** answers: *How is it built? What are the components?*
- Component table (Location, Responsibility, Dependencies) tells Claude where code goes
- Data flow diagrams show the critical paths
- "Boundaries and Invariants" are rules that can never be violated

**ROADMAP.md** answers: *What do we build? In what order? What's done?*
- This is the MOST IMPORTANT file for planning quality
- Each deliverable should map to 1-3 AIDLC issues
- Acceptance criteria here become issue acceptance criteria
- The dependency graph tells AIDLC what blocks what
- "Out of Scope" prevents AIDLC from adding unrequested features

**DESIGN.md** answers: *Why these patterns? How should code look?*
- Code examples are critical — Claude follows patterns it sees
- The "Anti-Patterns to Avoid" section prevents common mistakes
- API conventions ensure consistent endpoint design

**CLAUDE.md** answers: *How should the AI work with this codebase?*
- Direct, imperative instructions ("DO" / "DO NOT")
- Naming conventions table prevents inconsistency
- Test command so AIDLC can verify implementations
- Approved dependencies list prevents bloat

### Priority 1 (Detailed Blueprints)

| File                               | Purpose                    |
|-------------------------------------|----------------------------|
| `planning/COMPLETION_CHECKLIST.md`  | 100% completion tracker    |
| `planning/CONSTRAINTS.md`          | Hard technical constraints |
| `specs/FEATURE_TEMPLATE.md`        | Template for feature specs |
| `specs/data-model.md`              | Database schema            |
| `specs/{feature-name}.md`          | One file per feature       |
| `design/ERROR_HANDLING.md`         | Error taxonomy + patterns  |
| `design/TESTING_STRATEGY.md`       | How tests should look      |

**COMPLETION_CHECKLIST.md** — The "collectibles tracker." Every checkable item
maps to work that needs doing. AIDLC reads unchecked items as remaining work.

**CONSTRAINTS.md** — Performance targets, security requirements, dependency
limits. These become non-negotiable requirements on every issue.

**Feature specs** — One file per major feature. The more detail here (user
stories, data model, edge cases), the better Claude's implementation. Copy
`FEATURE_TEMPLATE.md` for each new feature.

### Priority 2 (Reference Material)

| File                    | Purpose                             |
|--------------------------|-------------------------------------|
| `docs/API_REFERENCE.md`  | Full endpoint documentation         |
| `docs/GLOSSARY.md`       | Domain terms → code names mapping   |

## How to Fill Out Templates

### Step 1: Start with README.md + ROADMAP.md (30 minutes)

These two files give AIDLC 80% of what it needs. Fill out:
- What the project does (3-5 sentences)
- What it does NOT do (scope boundaries)
- Phase 1 deliverables with acceptance criteria
- Key technical decisions

### Step 2: Add ARCHITECTURE.md + DESIGN.md (30 minutes)

Describe the system structure and coding patterns:
- Component diagram
- Data flow for critical paths
- 2-3 code patterns with examples
- Anti-patterns to avoid

### Step 3: Write CLAUDE.md (15 minutes)

Direct instructions for the AI:
- Language, test command, lint command
- DO / DO NOT rules
- Naming conventions

### Step 4: Fill out specs/ for each feature (15-30 min each)

One file per feature:
- User stories with acceptance criteria
- Data model changes
- API endpoints with request/response examples
- Edge cases table

### Step 5: Complete planning/ docs (15 minutes)

- COMPLETION_CHECKLIST.md — full list of everything needed
- CONSTRAINTS.md — performance, security, compatibility requirements

## Measuring Document Quality

### Good acceptance criteria (AIDLC can verify these):

```
- [ ] POST /api/register returns 201 with user_id
- [ ] Duplicate email returns 409 with CONFLICT error code
- [ ] Password under 8 chars returns 400
- [ ] Test coverage for auth module exceeds 90%
```

### Bad acceptance criteria (too vague for AIDLC):

```
- [ ] Registration works
- [ ] Handles errors properly
- [ ] Has good performance
- [ ] Is well-tested
```

### The Specificity Rule

**If a human QA tester couldn't verify the criterion in under 60 seconds,
it's not specific enough.** Rewrite it with concrete inputs, outputs, and
observable behavior.

## Recommended Workflow

```
1. Fill out templates          (1-2 hours for a medium project)
2. Run: aidlc run --plan-only  (review generated issues)
3. Refine docs based on issues (fix gaps, add detail)
4. Run: aidlc run              (full planning + implementation)
5. Review, iterate, re-run     (improve docs → better issues → better code)
```

## File Size Guidelines

| File                    | Target size  | Why                               |
|-------------------------|-------------|-----------------------------------|
| README.md               | 3-5 KB      | Dense overview, not a novel       |
| ARCHITECTURE.md         | 4-7 KB      | Components + diagrams             |
| ROADMAP.md              | 5-8 KB      | Detailed phases with AC           |
| DESIGN.md               | 5-8 KB      | Patterns with code examples       |
| CLAUDE.md               | 3-5 KB      | Concise rules                     |
| Feature spec            | 3-6 KB      | One feature, complete spec        |
| planning/ files          | 3-5 KB each | Focused on one concern            |
| docs/ files             | 3-8 KB each | Reference, not prose              |
| **Total**               | **40-60 KB** | Well within 80KB planning budget  |
