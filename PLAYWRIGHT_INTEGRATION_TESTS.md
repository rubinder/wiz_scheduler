# Playwright Integration Tests

End-to-end browser testing for WizScheduler scheduling workflows.

## Overview

`playwright_integration_tests.py` uses the seeded test company to verify:
- ✅ Manager login and authentication
- ✅ Schedule generation (single and multi-location)
- ✅ **Cross-location scheduling constraints** (core: no employee double-booking)
- ✅ Scheduling strategy selection (balanced, greedy, etc.)
- ✅ Schedule approval workflow
- ✅ Availability window enforcement
- ✅ Schedule export (CSV/PDF)

## Prerequisites

### 1. Database Seeding

First, seed the test company:
```bash
python seed_integration_test.py
```

Outputs test IDs:
```
COMPANY_ID:    integ-test-001
LOCATION_1_ID: integ-loc-001
LOCATION_2_ID: integ-loc-002
```

### 2. Frontend Running

Start the frontend dev server:
```bash
cd frontend && npm run dev
# Runs on http://localhost:5173
```

### 3. Backend Running

Backend must be running (API at `http://localhost:8000`):
```bash
cd backend && uvicorn main:app --reload
```

### 4. Test Credentials

The seeded test company includes a manager user. Set environment:
```bash
export TEST_MANAGER_EMAIL="manager@integ-test.local"
export TEST_MANAGER_PASSWORD="integ-test-password"  # Or fetch from Parameter Store
```

## Installation

```bash
pip install -r requirements_playwright.txt
playwright install  # Download Chromium, Firefox, WebKit
```

## Running Tests

### Local Development

```bash
# Headless (fast, CI mode)
pytest playwright_integration_tests.py -v

# Headed (see browser, debug mode)
pytest playwright_integration_tests.py -v --headed

# Specific test
pytest playwright_integration_tests.py::test_no_cross_location_overlap -v --headed

# Specific browser
pytest playwright_integration_tests.py -v --browser firefox
```

### CI/CD Pipeline

```bash
# Headless, all browsers
pytest playwright_integration_tests.py \
  -v \
  --tb=short \
  --browser chromium
```

## Key Tests

### 🔴 `test_no_cross_location_overlap` (Critical)

**What it tests:**
- Generates schedule for both locations simultaneously
- Verifies no employee is scheduled at Location 1 and Location 2 with overlapping times
- This is the core constraint that `availability_draft` enforces

**Why it matters:**
- Tests the deep copy mutation logic in LangGraph scheduling pipeline
- 40 of the 55 seeded employees are assigned to both locations, creating high contention
- Failure here indicates availability_draft or cross-location logic is broken

**Example failure:**
```
AssertionError: Employee emp-abc123 scheduled at integ-loc-001 and 
integ-loc-002 with overlapping times: 
2026-10-06 09:00:00+00:00–13:00:00+00:00 vs 2026-10-06 10:00:00+00:00–14:00:00+00:00
```

### `test_schedule_generation_both_locations`

Verifies the schedule page handles multi-location generation without errors.

### `test_schedule_strategies`

Tests multiple scheduling algorithms (balanced, greedy) produce valid output.

### `test_employee_availability_respected`

Confirms all shifts fall within the seeded 9am–5pm availability window.

### `test_schedule_approval_flow`

End-to-end: generate → review → approve.

## Customization

### Adjust Test Locations

Edit the hardcoded IDs at the top to match your seeded data:
```python
TEST_LOCATION_1_ID = "integ-loc-001"
TEST_LOCATION_2_ID = "integ-loc-002"
```

### Add Page Selectors

If the UI uses different selectors, update helper functions:
```python
async def generate_schedule(page: Page, ...) -> dict:
    # Match your actual button/input names
    await page.click('button:has-text("Generate Schedule")')
```

### Adjust Wait Timeouts

For slow environments, increase timeouts:
```python
await page.wait_for_selector('[data-schedule-status="complete"]', timeout=60000)  # 60s
```

## Troubleshooting

### "Could not find manager@integ-test.local"

The test user wasn't created during seeding. Re-run `seed_integration_test.py` or create the user manually:
```sql
INSERT INTO users (id, company_id, email, hashed_password, full_name, user_role, email_verified_at)
VALUES ('...', 'integ-test-001', 'manager@integ-test.local', '...', 'Test Manager', 'manager', now());
```

### "Timeout waiting for schedule completion"

Schedule generation is slow:
- Check backend logs for errors
- Increase timeout in the test
- Verify test data (availability, roles, etc.) is complete

### "Element not found: [data-shift-row]"

UI renders shifts differently:
- Use Playwright Inspector to debug: `PWDEBUG=1 pytest ...`
- Update selectors in `get_scheduled_shifts()` to match actual HTML

### "Browser crashes on headless"

Try headed mode to see errors:
```bash
pytest playwright_integration_tests.py -v --headed
```

## Data Isolation

Tests share the seeded `integ-test-001` company. If tests conflict:

**Option 1**: Seed fresh before each test run
```bash
python seed_integration_test.py && pytest playwright_integration_tests.py
```

**Option 2**: Use separate test companies
```python
TEST_COMPANY_ID = os.environ.get("TEST_COMPANY_ID", f"integ-test-{uuid4()}")
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Playwright Integration Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    services:
      postgres:
        image: postgres:15
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
        env:
          POSTGRES_PASSWORD: postgres
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install backend
        run: |
          cd backend
          pip install -r requirements.txt
      
      - name: Start backend (background)
        run: |
          cd backend
          uvicorn main:app --host 0.0.0.0 --port 8000 &
          sleep 5
      
      - name: Install frontend
        run: |
          cd frontend
          npm ci
      
      - name: Start frontend (background)
        run: |
          cd frontend
          npm run dev &
          sleep 5
      
      - name: Seed test data
        run: python seed_integration_test.py
      
      - name: Install Playwright dependencies
        run: |
          pip install -r requirements_playwright.txt
          playwright install --with-deps chromium
      
      - name: Run Playwright tests
        run: pytest playwright_integration_tests.py -v
      
      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: playwright-report
          path: playwright-report/
```

## Extending Tests

### Add a New Test

```python
@pytest.mark.asyncio
async def test_my_feature(page: Page) -> None:
    """Test description."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    
    # Your test steps
    await page.goto(f"{TEST_BASE_URL}/some/path")
    await page.click("button")
    
    # Assertions
    content = await page.text_content("[data-result]")
    assert "Expected text" in content
```

### Capture Screenshots on Failure

```python
async def test_example(page: Page) -> None:
    try:
        await page.click("button")
    except Exception:
        await page.screenshot(path=f"screenshots/failure-{datetime.now().isoformat()}.png")
        raise
```

---

**Last updated**: 2026-10-03
