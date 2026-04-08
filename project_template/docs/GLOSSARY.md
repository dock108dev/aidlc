# Glossary

<!-- AIDLC Priority: 2. Domain-specific terminology. Prevents AIDLC from
     misinterpreting domain terms or using inconsistent names in generated code. -->

## Domain Terms

<!-- Define every domain-specific term used in the project.
     Include the code-level name so AIDLC uses consistent naming. -->

| Term              | Code name       | Definition                                      |
|-------------------|-----------------|-------------------------------------------------|
| {e.g., Workspace} | `Workspace`    | {A container for related projects owned by a team} |
| {e.g., Pipeline}  | `Pipeline`     | {A sequence of processing steps applied to data} |
| {e.g., Artifact}  | `Artifact`     | {A file or dataset produced by a pipeline step}  |
| {e.g., Trigger}   | `Trigger`      | {An event that starts a pipeline execution}      |
| {e.g., Run}       | `PipelineRun`  | {A single execution of a pipeline}               |

## Abbreviations

| Abbreviation | Expansion           | Context                    |
|-------------|---------------------|----------------------------|
| {AC}        | Acceptance Criteria  | Issue specifications       |
| {API}       | Application Programming Interface | HTTP endpoints  |
| {CRUD}      | Create Read Update Delete | Standard operations    |

## Naming Conventions in Code

<!-- Map human concepts to code names so AIDLC generates consistent code. -->

| Human concept           | Variable name    | Class name      | DB table        |
|-------------------------|------------------|-----------------|-----------------|
| {A user of the system}  | `user`           | `User`          | `users`         |
| {A team workspace}      | `workspace`      | `Workspace`     | `workspaces`    |
| {A processing pipeline} | `pipeline`       | `Pipeline`      | `pipelines`     |

## Status Values

<!-- If entities have statuses, define them here so AIDLC uses consistent values. -->

### {Entity} Statuses

| Status      | Description                          | Can transition to         |
|-------------|--------------------------------------|---------------------------|
| `draft`     | Created but not activated            | active, cancelled         |
| `active`    | Currently in use                     | paused, completed         |
| `paused`    | Temporarily halted                   | active, cancelled         |
| `completed` | Successfully finished                | (terminal)                |
| `cancelled` | Terminated before completion         | (terminal)                |
