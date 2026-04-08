# Testing Strategy

<!-- AIDLC Priority: 1. Defines exactly how tests should be written.
     AIDLC uses this to generate test code alongside every implementation issue. -->

## Test Pyramid

```
         ╱╲           E2E / API Tests (few, slow, high confidence)
        ╱────╲
       ╱  Int  ╲       Integration Tests (moderate count, DB/services)
      ╱──────────╲
     ╱    Unit    ╲     Unit Tests (many, fast, isolated)
    ╱──────────────╲
```

| Layer          | Count    | Speed    | Dependencies  | Coverage target |
|----------------|----------|----------|---------------|-----------------|
| Unit           | Many     | < 1ms   | None (mocked) | > 90%           |
| Integration    | Moderate | < 500ms | Real DB/cache | Core paths      |
| API / E2E      | Few      | < 2s    | Full stack    | Critical flows  |

## Directory Structure

```
tests/
├── conftest.py              # Shared fixtures (DB, factories, auth helpers)
├── unit/
│   ├── conftest.py          # Unit-specific fixtures (mocks)
│   ├── test_{module_a}.py   # Tests for src/{module_a}/
│   └── test_{module_b}.py   # Tests for src/{module_b}/
├── integration/
│   ├── conftest.py          # DB setup/teardown, test server
│   ├── test_{feature_a}.py  # Cross-module integration
│   └── test_{feature_b}.py
└── api/
    ├── conftest.py          # API client fixture
    └── test_{endpoint}.py   # Full HTTP request/response
```

## Naming Convention

```
test_{what}_{scenario}_{expected_result}
```

**Examples:**
```
test_register_user_with_valid_email_returns_201
test_register_user_with_duplicate_email_returns_409
test_register_user_with_empty_password_returns_400
test_get_user_when_not_found_returns_404
test_delete_user_marks_as_soft_deleted
```

## Test Patterns

### Unit Test Template

```{language}
class TestUserService:
    """Tests for UserService.register()"""

    def test_register_with_valid_data_creates_user(self):
        # Arrange
        repo = MockUserRepository()
        service = UserService(repo)

        # Act
        user = service.register("test@example.com", "securepass123")

        # Assert
        assert user.email == "test@example.com"
        assert repo.saved_users == [user]

    def test_register_with_duplicate_email_raises_conflict(self):
        # Arrange
        repo = MockUserRepository(existing_emails=["taken@example.com"])
        service = UserService(repo)

        # Act & Assert
        with pytest.raises(ConflictError, match="already exists"):
            service.register("taken@example.com", "password123")

    def test_register_hashes_password(self):
        # Arrange
        repo = MockUserRepository()
        service = UserService(repo)

        # Act
        user = service.register("test@example.com", "plaintext")

        # Assert
        assert user.password_hash != "plaintext"
        assert verify_password("plaintext", user.password_hash)
```

### Integration Test Template

```{language}
class TestUserRegistrationIntegration:
    """Integration tests hitting real database."""

    def test_full_registration_flow(self, db_session):
        # Arrange
        repo = UserRepository(db_session)
        service = UserService(repo)

        # Act
        user = service.register("new@example.com", "password123")

        # Assert — verify in database
        db_user = db_session.query(User).filter_by(id=user.id).first()
        assert db_user is not None
        assert db_user.email == "new@example.com"
        assert db_user.email_verified is False
```

### API Test Template

```{language}
class TestRegistrationEndpoint:
    """Full HTTP tests for POST /api/register."""

    def test_successful_registration(self, api_client):
        response = api_client.post("/api/register", json={
            "email": "new@example.com",
            "password": "securepass123",
        })
        assert response.status_code == 201
        data = response.json()
        assert "user_id" in data["data"]

    def test_invalid_email_format(self, api_client):
        response = api_client.post("/api/register", json={
            "email": "not-an-email",
            "password": "password123",
        })
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
```

## Fixtures & Factories

### Fixture Guidelines

```{language}
# conftest.py — define reusable fixtures

@pytest.fixture
def user_factory():
    """Creates User objects with sensible defaults."""
    def _create(email="test@example.com", role="user", **overrides):
        defaults = {
            "id": str(uuid4()),
            "email": email,
            "password_hash": hash_password("defaultpass"),
            "display_name": "Test User",
            "role": role,
        }
        defaults.update(overrides)
        return User(**defaults)
    return _create

@pytest.fixture
def authenticated_client(api_client, user_factory):
    """API client with a valid auth token."""
    user = user_factory()
    token = create_token(user)
    api_client.headers["Authorization"] = f"Bearer {token}"
    return api_client
```

## What Every Test Must Have

1. **Clear name** describing behavior, not implementation
2. **Single assertion focus** — one logical thing per test
3. **No external dependencies** in unit tests (mock everything)
4. **Deterministic** — no random data, no time-dependent assertions
5. **Independent** — can run in any order, no shared state
6. **Fast** — unit < 1ms, integration < 500ms, API < 2s

## What NOT to Test

- Framework routing declarations (tested by the framework)
- Simple getters/setters with no logic
- Third-party library internals
- Private methods directly (test via public interface)
- Exact log messages (test that errors are logged, not exact wording)
