# Data Model

<!-- AIDLC Priority: 1. The complete data model for the project.
     AIDLC uses this to generate correct schema migrations, repository
     methods, and validation logic. Like the "world bible" for a game. -->

## Entity Relationship Overview

```
┌──────────┐     ┌──────────────┐     ┌──────────┐
│  {User}  │────▶│ {UserProfile} │     │ {Config}  │
└──────────┘     └──────────────┘     └──────────┘
      │
      │ has many
      ▼
┌──────────┐     ┌──────────────┐
│ {Entity1} │───▶│  {Entity2}   │
└──────────┘     └──────────────┘
```

## Tables

### `{users}`

| Column         | Type          | Constraints                      | Notes                |
|----------------|---------------|----------------------------------|----------------------|
| `id`           | UUID          | PK, DEFAULT gen_random_uuid()    |                      |
| `email`        | VARCHAR(255)  | UNIQUE, NOT NULL                 | Lowercased on insert |
| `password_hash`| VARCHAR(255)  | NOT NULL                         | bcrypt, cost 12      |
| `display_name` | VARCHAR(100)  | NOT NULL                         |                      |
| `role`         | VARCHAR(20)   | NOT NULL, DEFAULT 'user'         | user / admin         |
| `email_verified`| BOOLEAN      | NOT NULL, DEFAULT false          |                      |
| `created_at`   | TIMESTAMPTZ   | NOT NULL, DEFAULT now()          |                      |
| `updated_at`   | TIMESTAMPTZ   | NOT NULL, DEFAULT now()          | Trigger on update    |
| `deleted_at`   | TIMESTAMPTZ   | NULL                             | Soft delete          |

**Indexes:**
- `idx_users_email` UNIQUE on `email` WHERE `deleted_at IS NULL`
- `idx_users_created` on `created_at`

---

### `{entity_1}`

| Column         | Type          | Constraints                      | Notes                |
|----------------|---------------|----------------------------------|----------------------|
| `id`           | UUID          | PK                               |                      |
| `user_id`      | UUID          | FK → users(id), NOT NULL         | Owner                |
| `{field_1}`    | {TYPE}        | {CONSTRAINTS}                    | {Description}        |
| `{field_2}`    | {TYPE}        | {CONSTRAINTS}                    | {Description}        |
| `status`       | VARCHAR(20)   | NOT NULL, DEFAULT '{default}'    | {enum values}        |
| `created_at`   | TIMESTAMPTZ   | NOT NULL, DEFAULT now()          |                      |
| `updated_at`   | TIMESTAMPTZ   | NOT NULL, DEFAULT now()          |                      |

**Indexes:**
- `idx_{table}_user_id` on `user_id`
- `idx_{table}_status` on `status`

**Foreign Keys:**
- `user_id` → `users(id)` ON DELETE CASCADE

---

### `{entity_2}`

| Column         | Type          | Constraints                      | Notes                |
|----------------|---------------|----------------------------------|----------------------|
| `id`           | UUID          | PK                               |                      |
| `{parent}_id`  | UUID          | FK → {entity_1}(id), NOT NULL   | Parent reference     |
| `{field_1}`    | {TYPE}        | {CONSTRAINTS}                    | {Description}        |
| `created_at`   | TIMESTAMPTZ   | NOT NULL, DEFAULT now()          |                      |

## Enums / Lookup Values

### `{entity_status}`

| Value       | Description                      | Transitions from        |
|-------------|----------------------------------|-------------------------|
| `draft`     | Not yet active                   | (initial)               |
| `active`    | Live and operational             | draft                   |
| `paused`    | Temporarily inactive             | active                  |
| `completed` | Successfully finished            | active                  |
| `cancelled` | Terminated early                 | draft, active, paused   |

## Migration Strategy

- Use {Alembic / Knex / diesel / goose / raw SQL}
- Migrations are sequential: `{001_create_users.sql}`, `{002_create_entities.sql}`
- Always provide UP and DOWN migration
- Never modify existing data in schema migrations — use separate data migrations
- Test migrations against empty DB AND against production snapshot

## Seed Data

<!-- Data that must exist for the system to work. -->

```sql
-- Default admin user (password: change-me-immediately)
INSERT INTO users (id, email, password_hash, display_name, role, email_verified)
VALUES ('{uuid}', 'admin@example.com', '{bcrypt_hash}', 'Admin', 'admin', true);

-- Default configuration
INSERT INTO config (key, value) VALUES
  ('max_items_per_user', '100'),
  ('default_timezone', 'UTC');
```

## Query Patterns

<!-- Common queries that need indexes. AIDLC uses these to generate
     correct repository implementations. -->

| Query                               | Frequency | Index                       |
|-------------------------------------|-----------|------------------------------|
| Get user by email                   | Very high | `idx_users_email`           |
| List user's {entities} by status    | High      | `idx_{table}_user_id_status`|
| Get {entity} with all {children}    | High      | FK index + JOIN             |
| Search {entities} by {field}        | Medium    | `idx_{table}_{field}`       |
| Count {entities} by status          | Low       | `idx_{table}_status`        |
