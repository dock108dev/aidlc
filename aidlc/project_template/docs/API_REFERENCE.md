# API Reference

<!-- AIDLC Priority: 2. Full API contract. AIDLC uses this during implementation
     to generate endpoints that match the spec exactly. -->

## Base URL

```
{Development}: http://localhost:{port}/api/v1
{Production}:  https://api.{domain}/v1
```

## Authentication

All authenticated endpoints require:
```
Authorization: Bearer {access_token}
```

Token is obtained via `POST /auth/login`. Tokens expire after {duration}.

## Common Headers

| Header          | Required | Value                        |
|-----------------|----------|------------------------------|
| Content-Type    | Yes      | application/json             |
| Authorization   | Varies   | Bearer {token}               |
| X-Request-ID    | No       | Client-generated UUID        |

## Endpoints

### Authentication

#### `POST /auth/register`

Create a new user account.

**Auth:** None

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securePassword123",
  "display_name": "Jane Doe"
}
```

**Validation:**
- `email`: required, valid email format, max 255 chars
- `password`: required, min 8 chars
- `display_name`: required, max 100 chars

**Response 201:**
```json
{
  "data": {
    "user_id": "uuid",
    "email": "user@example.com",
    "display_name": "Jane Doe"
  }
}
```

**Errors:** 400 (validation), 409 (email exists)

---

#### `POST /auth/login`

Authenticate and receive tokens.

**Auth:** None

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securePassword123"
}
```

**Response 200:**
```json
{
  "data": {
    "access_token": "jwt...",
    "refresh_token": "jwt...",
    "expires_in": 900
  }
}
```

**Errors:** 401 (invalid credentials)

---

### {Resource: e.g., Items}

#### `GET /{resources}`

List {resources} with pagination.

**Auth:** Required

**Query Parameters:**

| Param     | Type   | Default | Description                   |
|-----------|--------|---------|-------------------------------|
| `page`    | int    | 1       | Page number                   |
| `per_page`| int    | 20      | Items per page (max 100)      |
| `sort`    | string | -created_at | Sort field (prefix - for desc)|
| `status`  | string | null    | Filter by status              |
| `q`       | string | null    | Search query                  |

**Response 200:**
```json
{
  "data": [
    {
      "id": "uuid",
      "{field}": "{value}",
      "created_at": "2024-01-15T09:30:00Z"
    }
  ],
  "meta": {
    "page": 1,
    "per_page": 20,
    "total": 42,
    "total_pages": 3
  }
}
```

---

#### `GET /{resources}/{id}`

Get a single {resource}.

**Auth:** Required (owner or admin)

**Response 200:**
```json
{
  "data": {
    "id": "uuid",
    "{field}": "{value}",
    "{nested}": { ... },
    "created_at": "2024-01-15T09:30:00Z",
    "updated_at": "2024-01-15T10:00:00Z"
  }
}
```

**Errors:** 401, 403, 404

---

#### `POST /{resources}`

Create a new {resource}.

**Auth:** Required

**Request:**
```json
{
  "{field_1}": "{value}",
  "{field_2}": "{value}"
}
```

**Validation:**
- `{field_1}`: required, {constraints}
- `{field_2}`: optional, {constraints}

**Response 201:**
```json
{
  "data": {
    "id": "uuid",
    "{field_1}": "{value}",
    "created_at": "2024-01-15T09:30:00Z"
  }
}
```

**Errors:** 400 (validation), 401, 409 (conflict)

---

#### `PUT /{resources}/{id}`

Update a {resource}. Partial updates supported.

**Auth:** Required (owner or admin)

**Request:**
```json
{
  "{field_1}": "{new_value}"
}
```

**Response 200:** Updated resource object

**Errors:** 400, 401, 403, 404

---

#### `DELETE /{resources}/{id}`

Soft-delete a {resource}.

**Auth:** Required (owner or admin)

**Response 204:** No content

**Errors:** 401, 403, 404

## Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": []
  }
}
```

See `design/ERROR_HANDLING.md` for full error taxonomy.
