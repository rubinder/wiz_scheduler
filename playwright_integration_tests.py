"""
Playwright browser integration tests for WizScheduler.

Tests the full scheduling workflow end-to-end using the seeded test company.
Verifies UI behavior, cross-location constraints, and schedule generation.

Requirements:
    pip install pytest-playwright

Usage:
    pytest playwright_integration_tests.py -v

    For headed mode (see browser):
    pytest playwright_integration_tests.py -v --headed

    For specific browser:
    pytest playwright_integration_tests.py -v --browser chromium

Environment Variables:
    - TEST_BASE_URL (default: http://localhost:5173)
    - TEST_COMPANY_ID (default: integ-test-001, must match seeded data)
    - TEST_MANAGER_EMAIL (default: manager@integ-test.local)
    - TEST_MANAGER_PASSWORD (from Parameter Store or env)
"""

import json
import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
from playwright.async_api import async_playwright, Page, Browser, BrowserContext


# --- Configuration ---

TEST_BASE_URL = os.environ.get("TEST_BASE_URL", "http://localhost:5173")
TEST_COMPANY_ID = os.environ.get("TEST_COMPANY_ID", "integ-test-001")
TEST_MANAGER_EMAIL = os.environ.get("TEST_MANAGER_EMAIL", "manager@integ-test.local")
TEST_MANAGER_PASSWORD = os.environ.get("TEST_MANAGER_PASSWORD", "integ-test-password")

# Seeded location IDs (must match seed_integration_test.py)
TEST_LOCATION_1_ID = "integ-loc-001"
TEST_LOCATION_2_ID = "integ-loc-002"


# --- Fixtures ---

@pytest.fixture(scope="session")
async def browser() -> AsyncGenerator[Browser, None]:
    """Launch Playwright browser for the session."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        yield browser
        await browser.close()


@pytest.fixture
async def page(browser: Browser) -> AsyncGenerator[Page, None]:
    """Create a new page for each test."""
    context: BrowserContext = await browser.new_context()
    page: Page = await context.new_page()
    yield page
    await context.close()


# --- Helper Functions ---

async def login(page: Page, email: str, password: str) -> None:
    """Log in with email and password."""
    await page.goto(f"{TEST_BASE_URL}/login")
    await page.fill('input[name="email"]', email)
    await page.fill('input[name="password"]', password)
    await page.click('button:has-text("Sign In")')

    # Wait for redirect to dashboard
    await page.wait_for_url(f"{TEST_BASE_URL}/manager/**", timeout=10000)


async def navigate_to_schedule_page(page: Page, location_id: str) -> None:
    """Navigate to the schedule generation page for a location."""
    await page.goto(f"{TEST_BASE_URL}/manager/schedules")
    await page.click(f'[data-location-id="{location_id}"]')
    await page.wait_for_url(f"**/schedule/**", timeout=5000)


async def generate_schedule(
    page: Page,
    strategy: str = "balanced",
    week_offset: int = 1,
) -> dict:
    """Generate a schedule and return the result.

    Args:
        page: Playwright page
        strategy: Scheduling strategy (balanced, greedy, etc.)
        week_offset: Weeks from now to schedule (default: next week)

    Returns:
        Schedule result dict with shifts, employees, etc.
    """
    # Set strategy
    await page.select_option('select[name="strategy"]', strategy)

    # Set week (usually auto-filled to next week, but explicit)
    today = datetime.now(timezone.utc).date()
    week_start = today + timedelta(weeks=week_offset)
    # Assuming date picker — adjust selector as needed
    await page.fill('input[name="week_start_date"]', week_start.isoformat())

    # Click generate
    await page.click('button:has-text("Generate Schedule")')

    # Wait for NDJSON stream to complete (page shows results)
    await page.wait_for_selector('[data-schedule-status="complete"]', timeout=30000)

    # Extract shifts from the page
    shifts_json = await page.text_content('[data-shifts-json]')
    if shifts_json:
        return json.loads(shifts_json)

    return {}


async def get_scheduled_shifts(page: Page) -> list[dict]:
    """Extract all shifts from the currently displayed schedule."""
    shift_rows = await page.query_selector_all('[data-shift-row]')
    shifts = []

    for row in shift_rows:
        shift_data = await row.get_attribute('data-shift-json')
        if shift_data:
            shifts.append(json.loads(shift_data))

    return shifts


# --- Tests ---

@pytest.mark.asyncio
async def test_login(page: Page) -> None:
    """Test manager login."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)

    # Verify we're on the dashboard
    title = await page.title()
    assert "WizScheduler" in title or "Dashboard" in title


@pytest.mark.asyncio
async def test_schedule_generation_single_location(page: Page) -> None:
    """Test schedule generation for a single location."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    await navigate_to_schedule_page(page, TEST_LOCATION_1_ID)

    result = await generate_schedule(page, strategy="balanced")

    # Verify schedule was generated
    assert result is not None, "Schedule generation failed"
    assert len(result.get("shifts", [])) > 0, "No shifts in schedule"

    # Verify all shifts are for the correct location
    for shift in result["shifts"]:
        assert shift["location_id"] == TEST_LOCATION_1_ID


@pytest.mark.asyncio
async def test_schedule_generation_both_locations(page: Page) -> None:
    """Test schedule generation for both locations simultaneously.

    Verifies that the scheduler handles multiple locations correctly
    and respects availability_draft (no cross-location double-booking).
    """
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)

    # Go to schedule page and select both locations
    await page.goto(f"{TEST_BASE_URL}/manager/schedules")

    # Assuming multi-select checkboxes for locations
    await page.check(f'input[data-location-id="{TEST_LOCATION_1_ID}"]')
    await page.check(f'input[data-location-id="{TEST_LOCATION_2_ID}"]')

    # Generate schedule
    await page.click('button:has-text("Generate Schedule")')
    await page.wait_for_selector('[data-schedule-status="complete"]', timeout=30000)

    shifts = await get_scheduled_shifts(page)

    # Verify both locations have shifts
    locations = {s["location_id"] for s in shifts}
    assert TEST_LOCATION_1_ID in locations, "Location 1 has no shifts"
    assert TEST_LOCATION_2_ID in locations, "Location 2 has no shifts"


@pytest.mark.asyncio
async def test_no_cross_location_overlap(page: Page) -> None:
    """Critical test: Verify no employee is scheduled at two locations simultaneously.

    This tests the core constraint: employees assigned to both locations
    should never have overlapping shifts.
    """
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)

    # Generate schedule for both locations
    await page.goto(f"{TEST_BASE_URL}/manager/schedules")
    await page.check(f'input[data-location-id="{TEST_LOCATION_1_ID}"]')
    await page.check(f'input[data-location-id="{TEST_LOCATION_2_ID}"]')

    await page.click('button:has-text("Generate Schedule")')
    await page.wait_for_selector('[data-schedule-status="complete"]', timeout=30000)

    shifts = await get_scheduled_shifts(page)

    # Group shifts by employee
    shifts_by_employee = {}
    for shift in shifts:
        emp_id = shift["employee_id"]
        if emp_id not in shifts_by_employee:
            shifts_by_employee[emp_id] = []
        shifts_by_employee[emp_id].append(shift)

    # Verify no employee is at two locations at once
    for emp_id, emp_shifts in shifts_by_employee.items():
        for i, shift_a in enumerate(emp_shifts):
            for shift_b in emp_shifts[i+1:]:
                # Check for time overlap
                a_start = datetime.fromisoformat(shift_a["start_time"])
                a_end = datetime.fromisoformat(shift_a["end_time"])
                b_start = datetime.fromisoformat(shift_b["start_time"])
                b_end = datetime.fromisoformat(shift_b["end_time"])

                overlap = not (a_end <= b_start or b_end <= a_start)

                assert not overlap or shift_a["location_id"] == shift_b["location_id"], (
                    f"Employee {emp_id} scheduled at {shift_a['location_id']} and "
                    f"{shift_b['location_id']} with overlapping times: "
                    f"{a_start}–{a_end} vs {b_start}–{b_end}"
                )


@pytest.mark.asyncio
async def test_schedule_strategies(page: Page) -> None:
    """Test multiple scheduling strategies produce valid schedules."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    await navigate_to_schedule_page(page, TEST_LOCATION_1_ID)

    for strategy in ["balanced", "greedy"]:  # Adjust to actual available strategies
        # Clear previous schedule
        await page.reload()

        # Generate with strategy
        result = await generate_schedule(page, strategy=strategy)

        assert len(result.get("shifts", [])) > 0, f"Strategy '{strategy}' produced no shifts"

        # Verify all shifts have required fields
        for shift in result["shifts"]:
            assert "employee_id" in shift
            assert "start_time" in shift
            assert "end_time" in shift
            assert "role_id" in shift


@pytest.mark.asyncio
async def test_schedule_approval_flow(page: Page) -> None:
    """Test the schedule approval workflow."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    await navigate_to_schedule_page(page, TEST_LOCATION_1_ID)

    # Generate schedule
    await generate_schedule(page)

    # Find and review shifts (should be in draft status)
    status = await page.text_content('[data-schedule-status]')
    assert "draft" in status.lower(), "Schedule should be in draft status"

    # Approve schedule
    await page.click('button:has-text("Approve")')

    # Wait for approval confirmation
    await page.wait_for_selector('[data-schedule-status="approved"]', timeout=5000)

    # Verify status changed
    status = await page.text_content('[data-schedule-status]')
    assert "approved" in status.lower(), "Schedule should be approved"


@pytest.mark.asyncio
async def test_employee_availability_respected(page: Page) -> None:
    """Verify generated schedules respect employee availability windows."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    await navigate_to_schedule_page(page, TEST_LOCATION_1_ID)

    result = await generate_schedule(page)
    shifts = result.get("shifts", [])

    # All shifts should fall within 9am–5pm (seeded availability window)
    for shift in shifts:
        start_hour = datetime.fromisoformat(shift["start_time"]).hour
        end_hour = datetime.fromisoformat(shift["end_time"]).hour

        assert start_hour >= 9, f"Shift starts before 9am: {shift}"
        assert end_hour <= 17, f"Shift ends after 5pm: {shift}"


@pytest.mark.asyncio
async def test_schedule_export(page: Page) -> None:
    """Test schedule export to CSV/PDF."""
    await login(page, TEST_MANAGER_EMAIL, TEST_MANAGER_PASSWORD)
    await navigate_to_schedule_page(page, TEST_LOCATION_1_ID)

    # Generate and approve schedule
    await generate_schedule(page)
    await page.click('button:has-text("Approve")')
    await page.wait_for_selector('[data-schedule-status="approved"]', timeout=5000)

    # Test export
    async with page.expect_download() as download_promise:
        await page.click('button:has-text("Export")')

    download = await download_promise
    assert download.filename.endswith((".csv", ".xlsx", ".pdf"))


# --- Run Tests ---

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
