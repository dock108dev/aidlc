"""Prompt templates for AIDLC interactive planning doc generation."""


ROADMAP_GENERATION_PROMPT = """\
You are generating a ROADMAP.md for a software project. Use the project info below
to create a comprehensive, phased delivery plan.

## Project Info
- **Name**: {project_name}
- **Description**: {one_liner}
- **Type**: {project_type}
- **Tech Stack**: {tech_stack}
- **Target Audience**: {target_audience}
- **MVP Definition**: {mvp_definition}
- **Constraints**: {constraints}
- **Inspiration**: {inspiration}

## Core Features
{core_features}

## Requested Phases
{phases}

{existing_context}

## Instructions

Generate a ROADMAP.md with:
1. Each phase as a ## heading with a clear goal statement
2. Concrete deliverables as checkbox items (- [ ] item)
3. Exit criteria for each phase ("Phase is done when...")
4. Phases ordered from MVP to polish
5. Each item should be specific enough to create implementation issues from

If the project involves content creation (items, levels, characters, etc.),
include specific content targets (e.g., "20 items for store type X").

If the project involves parody/spoof content, note that ALL content must be
original — no real brand names, copyrighted characters, or trademarked terms.

Output ONLY the markdown document content. No wrapping or explanation.
"""


ARCHITECTURE_GENERATION_PROMPT = """\
You are generating an ARCHITECTURE.md for a software project.

## Project Info
- **Name**: {project_name}
- **Description**: {one_liner}
- **Type**: {project_type}
- **Tech Stack**: {tech_stack}

## Core Features
{core_features}

{existing_context}

## Instructions

Generate an ARCHITECTURE.md covering:
1. **Overview** — what the system is and how it's structured
2. **Key Components** — major modules/systems with responsibilities
3. **Data Flow** — how data moves through the system
4. **State Management** — how state is tracked and persisted
5. **Directory Structure** — expected file/folder layout

Tailor to the actual tech stack. For games: scene trees, managers, signals.
For web apps: frontend/backend split, API layers, database. For CLIs: command
structure, I/O handling.

Output ONLY the markdown document content.
"""


DESIGN_GENERATION_PROMPT = """\
You are generating a DESIGN.md for a software project.

## Project Info
- **Name**: {project_name}
- **Description**: {one_liner}
- **Type**: {project_type}
- **Tech Stack**: {tech_stack}
- **Constraints**: {constraints}

## Core Features
{core_features}

{existing_context}

## Instructions

Generate a DESIGN.md covering:
1. **Design Principles** — 3-5 core principles guiding decisions
2. **Patterns** — key architectural/design patterns to follow (with code examples)
3. **Anti-Patterns** — things to explicitly avoid
4. **Naming Conventions** — file, class, function naming standards
5. **Error Handling** — strategy for errors and edge cases
6. **Testing Strategy** — what to test, how, what frameworks

Tailor to the tech stack. Include code examples in the project's language.

Output ONLY the markdown document content.
"""


CLAUDE_MD_GENERATION_PROMPT = """\
You are generating a CLAUDE.md for a software project. This file tells AI coding
assistants how to work in this codebase.

## Project Info
- **Name**: {project_name}
- **Type**: {project_type}
- **Tech Stack**: {tech_stack}
- **Constraints**: {constraints}

{existing_context}

## Instructions

Generate a CLAUDE.md covering:
1. **Project Identity** — name, stack, description
2. **Style** — language-specific style guide, line length, formatting
3. **Naming** — file naming, class naming, variable naming conventions
4. **Testing** — test framework, how to run tests, test file conventions
5. **Dependencies** — what's allowed, what's banned, how to add new ones
6. **Git** — commit message format, branch naming, PR conventions
7. **Dev Setup** — how to clone, install, and run locally
8. **Important Rules** — project-specific constraints the AI must follow

Keep it practical and specific to this tech stack. No generic advice.

Output ONLY the markdown document content.
"""


RESEARCH_TRIGGER_PROMPT = """\
You are analyzing a project planning session to identify research needs.

## Project Info
- **Name**: {project_name}
- **Description**: {one_liner}
- **Type**: {project_type}
- **Tech Stack**: {tech_stack}
- **Inspiration**: {inspiration}

## Core Features
{core_features}

## User's Research Requests
{research_needs}

## Instructions

Identify ALL topics that need research before implementation can begin.
For each topic, provide a focused research question.

Categories to consider:
1. **Free APIs & Data Sources** — external data the project needs
2. **Existing Solutions** — GitHub repos, libraries, or tools that could help
3. **Content Design** — items, characters, levels, cards that need creative design
4. **Technical Patterns** — how to implement specific mechanics or systems
5. **Parody/Spoof Research** — understanding source material to create original alternatives
6. **Visual/Audio Direction** — art style, music style, UI patterns to follow

For parody/spoof content: research the SOURCE MATERIAL to understand what makes it
work, then design ORIGINAL alternatives. Never copy — transform and satirize.

Output a JSON array of research topics:
```json
[
  {{
    "topic": "short-kebab-case-name",
    "question": "Detailed research question",
    "category": "api|content|technical|creative|parody",
    "priority": "high|medium|low"
  }}
]
```
"""


REFINEMENT_SYSTEM_PROMPT = """\
You are helping a user refine their project documentation for {project_name}.

The following docs were just generated and may need expansion, correction, or detail:
- ROADMAP.md — phased delivery plan
- ARCHITECTURE.md — system structure
- DESIGN.md — patterns and conventions
- CLAUDE.md — AI coding instructions

You have full access to edit these files. The user will talk through their vision
and you should update the docs to reflect their decisions.

Key guidelines:
- Be specific — vague roadmap items become vague implementation
- Each ROADMAP item should be implementable as a single issue
- ARCHITECTURE.md should match the actual tech stack
- All creative content (names, brands, characters) must be ORIGINAL
- For parody/spoof: transform and satirize, never copy real IP

Research docs are in docs/research/ — reference them when relevant.

When the user asks you to review or audit, check:
- Are all phases detailed enough to create issues from?
- Are there missing systems or features?
- Does the architecture cover all core features?
- Are there content gaps that need research?
"""
