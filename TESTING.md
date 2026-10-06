# Testing Guidelines

WizScheduler uses a clear separation between unit and integration tests to enable efficient parallel CI execution and maintain test isolation.

## Test Categories

### Unit Tests
**Purpose:** Test pure business logic, utilities, and functions in isolation.

**Characteristics:**
- No database access (use in-memory state only)
- No HTTP client or AsyncClient fixtures
- No external dependencies
- Fast execution (~2-3 minutes for ~300 tests)
- Can run in parallel without conflicts

**File naming:** `test_*.py` (no suffix)

**Examples:**
- `test_signal_weight_resolution.py` — pure function testing
- `test_cost_seniority.py` — scoring algorithms
- `test_utc_today.py` — enforcer (AST sweep)
- `test_pagination.py` — pagination logic
- `test_email_normalize.py` — string normalization

**Marker:** None (default)

**Example:**
```python
"""Tests for email normalization logic."""

def test_normalize_removes_whitespace():
    result = normalize_email("  test@example.com  ")
    assert result == "test@example.com"

def test_normalize_lowercases():
    result = normalize_email("Test@EXAMPLE.COM")
    assert result == "test@example.com"
```

---

### Integration Tests
**Purpose:** Test full API endpoints, database interactions, and component integration.

**Characteristics:**
- Requires `client: AsyncClient` fixture to make HTTP requests
- Uses `db_session: AsyncSession` fixture for database state
- Tests full workflows with real dependencies
- Moderate execution (~72 seconds for ~150 active tests)
- Can run in parallel with proper test isolation (each gets fresh DB)

**File naming:** `test_*_integration.py` (mandatory `_integration` suffix)

**Marker:** `@pytest.mark.integration` (via `pytestmark` or decorator)

**Examples:**
- `test_auth_integration.py` — login/registration endpoints
- `test_billing_integration.py` — billing API endpoints
- `test_schedules_integration.py` — schedule generation & CRUD

**Example:**
```python
"""Tests for employee API endpoints."""

import pytest

pytestmark = pytest.mark.integration

async def test_create_employee(client: AsyncClient, manager_token: str, seed_location: Location):
    resp = await client.post(
        f"/api/v1/locations/{seed_location.id}/employees",
        json={"full_name": "Alice", "email": "alice@test.com"},
        headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["full_name"] == "Alice"
```

---

## How to Create a New Test

### 1. Determine the Category

**Ask:** Does this test need to:
- Make HTTP requests? → Integration
- Access the database directly? → Integration  
- Use fixtures like `client`, `db_session`, or seed data? → Integration
- Test pure functions only? → Unit

### 2. Unit Test
Create `test_myfeature.py`:
```python
"""Tests for myfeature logic."""

def test_my_function_happy_path():
    result = my_function(input)
    assert result == expected

def test_my_function_edge_case():
    result = my_function(edge_case)
    assert result == expected_edge_case
```

### 3. Integration Test
Create `test_myfeature_integration.py`:
```python
"""Tests for myfeature API endpoints."""

import pytest

pytestmark = pytest.mark.integration

async def test_endpoint_success(client: AsyncClient, manager_token: str):
    resp = await client.post(
        "/api/v1/myendpoint",
        json={"key": "value"},
        headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 201

async def test_endpoint_requires_auth(client: AsyncClient):
    resp = await client.post(
        "/api/v1/myendpoint",
        json={"key": "value"}
    )
    assert resp.status_code == 401
```

---

## CI Execution

The test suite runs in parallel using pytest markers:

```yaml
# Unit tests only
pytest tests/ -m "not integration" --tb=short
# Result: ~11 seconds, 304 tests

# Integration tests only
pytest tests/ -m "integration" --tb=short
# Result: ~72 seconds, 148+ tests

# Both run concurrently in separate matrix jobs (no duplication)
```

**Key principle:** Each matrix job runs exactly one category, not both. This prevents:
- Duplicate test execution
- Wasted CI time
- Cross-job interference

---

## Test Independence Best Practices

### ✅ DO

- **Use provided fixtures** (`db_session`, `client`, `seed_*`) — they handle cleanup
- **Create fresh test data** for each test via fixtures
- **Avoid global state** — each test should be independent
- **Clean up after yourself** — fixtures handle this automatically via `yield`
- **Use parametrize** for similar test cases:
  ```python
  @pytest.mark.parametrize("status", ["draft", "approved"])
  async def test_schedule_status(client, status):
      # Test runs twice, once per status
  ```

### ❌ DON'T

- **Depend on test execution order** — tests should pass in any order
- **Share state between tests** — each test gets a clean environment
- **Hardcode database IDs** — use fixtures or generate fresh ones
- **Mock the database** for integration tests — use the real SQLite test DB
- **Import test helpers from renamed files** without updating the import:
  ```python
  # ❌ WRONG (old name)
  from tests.test_auth import _make_token

  # ✅ CORRECT (new name)
  from tests.test_auth_integration import _make_token
  ```

---

## Shared Test Utilities

Common helpers live in `tests/conftest.py` and are available to all tests:

```python
# Fixtures
db_session: AsyncSession          # Fresh DB for each test
client: AsyncClient               # HTTP client connected to app
manager_token: str                # JWT for authenticated requests
seed_company: Company             # Pre-created test company
seed_location: Location           # Pre-created test location
seed_employees: list[Employee]    # Pre-created test employees

# Helpers
_id() -> str                       # Generate deterministic test IDs
_make_token(user_id, company_id, role) -> str  # Create JWT
```

Example usage:
```python
async def test_list_employees(client: AsyncClient, manager_token: str, seed_location: Location):
    resp = await client.get(
        f"/api/v1/locations/{seed_location.id}/employees",
        headers={"Authorization": f"Bearer {manager_token}"}
    )
    assert resp.status_code == 200
```

---

## Checklist for New Tests

- [ ] File named correctly (`test_*.py` for unit, `test_*_integration.py` for integration)
- [ ] Marker present on integration tests (`pytestmark = pytest.mark.integration`)
- [ ] Uses fixtures for setup (not hardcoded test data)
- [ ] Imports use current test file names (e.g., `test_auth_integration`)
- [ ] Test is idempotent (passes when run alone or with others)
- [ ] No sleep calls or timing dependencies
- [ ] Docstring explains what is being tested
- [ ] Assertions are specific (not just `assert result`)

---

## Running Tests Locally

```bash
# All unit tests
pytest tests/ -m "not integration" -v

# All integration tests
pytest tests/ -m "integration" -v

# Specific test file
pytest tests/test_auth_integration.py -v

# Specific test function
pytest tests/test_auth_integration.py::test_login_success -v

# Watch for failures
pytest tests/ -x -v  # Stop at first failure
```

---

## Migration from Old Files

When renaming test files (e.g., `test_auth.py` → `test_auth_integration.py`):

1. Update all imports across the test suite:
   ```bash
   grep -r "from tests.test_auth import" tests/
   # Update each occurrence to "test_auth_integration"
   ```

2. Add the marker at the top of the file (after docstring):
   ```python
   """Tests for auth endpoints."""
   
   import pytest
   
   pytestmark = pytest.mark.integration
   ```

3. Run both test categories to verify independence:
   ```bash
   pytest tests/ -m "not integration"
   pytest tests/ -m "integration"
   ```
