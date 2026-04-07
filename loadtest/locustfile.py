"""
WizScheduler load test — simulates 10,000 managers going through the full
workflow: register → setup data → create templates → generate schedules.

Usage:
    locust -f locustfile.py --host http://localhost:8000
    locust -f locustfile.py --host http://localhost:8000 --headless -u 10000 -r 500 --run-time 10m
"""

from __future__ import annotations

import json
import random
import string
from datetime import date, datetime, timedelta

from faker import Faker
from locust import HttpUser, between, task, events

fake = Faker()

# ── Helpers ──────────────────────────────────────────────────────────────────

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Toronto", "Europe/London",
    "Europe/Berlin", "Asia/Tokyo", "Australia/Sydney",
]
ROLE_NAMES = [
    "Server", "Bartender", "Host", "Busser", "Dishwasher", "Line Cook",
    "Prep Cook", "Sous Chef", "Head Chef", "Cashier", "Barista",
    "Floor Associate", "Team Lead", "Shift Supervisor", "Manager on Duty",
    "Delivery Driver", "Stocker", "Cleaner", "Security", "Receptionist",
]
STRATEGIES = ["random", "rotation", "rotation_history", "max_hours"]


def random_email() -> str:
    uid = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"lt_{uid}@loadtest.local"


def current_week_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


# ── Manager User ─────────────────────────────────────────────────────────────

class ManagerUser(HttpUser):
    """
    Simulates a single manager: registers, sets up their company, then loops
    on schedule generation.
    """
    wait_time = between(1, 3)

    # State populated during on_start
    token: str = ""
    headers: dict = {}
    region_ids: list[str] = []
    location_ids: list[str] = []
    role_ids: list[str] = []
    role_names_list: list[str] = []
    employee_ids: list[str] = []
    template_ids: list[str] = []

    # ── Lifecycle ────────────────────────────────────────────────────────

    def on_start(self):
        """Run full setup once per virtual user."""
        self.region_ids = []
        self.location_ids = []
        self.role_ids = []
        self.role_names_list = []
        self.employee_ids = []
        self.template_ids = []

        self._register()
        self._create_regions()
        self._create_locations()
        self._create_roles()
        self._bulk_upload_employees()
        self._set_availability()
        self._create_shift_templates()

    # ── Setup steps ──────────────────────────────────────────────────────

    def _register(self):
        email = random_email()
        password = "LoadTest1!"
        company_name = fake.company()[:60]

        with self.client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "full_name": fake.name(),
                "company_name": company_name,
                "privacy_accepted": True,
                "terms_accepted": True,
            },
            name="/api/v1/auth/register",
            catch_response=True,
        ) as resp:
            if resp.status_code == 201:
                data = resp.json()
                self.token = data["access_token"]
                self.headers = {
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                }
                resp.success()
            else:
                resp.failure(f"Register failed: {resp.status_code} {resp.text[:200]}")
                self.environment.runner.quit()

    def _create_regions(self):
        count = random.randint(1, 3)
        for _ in range(count):
            with self.client.post(
                "/api/v1/regions/",
                json={"name": fake.state()[:40], "geo_bounds": None},
                headers=self.headers,
                name="/api/v1/regions/ [create]",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201):
                    self.region_ids.append(resp.json()["id"])
                    resp.success()
                else:
                    resp.failure(f"Create region: {resp.status_code}")

    def _create_locations(self):
        for region_id in self.region_ids:
            loc_count = random.randint(1, 2)
            for _ in range(loc_count):
                tz = random.choice(TIMEZONES)
                with self.client.post(
                    "/api/v1/locations/",
                    json={
                        "region_id": region_id,
                        "name": f"{fake.city()} Store",
                        "address": fake.address().replace("\n", ", "),
                        "timezone": tz,
                    },
                    headers=self.headers,
                    name="/api/v1/locations/ [create]",
                    catch_response=True,
                ) as resp:
                    if resp.status_code in (200, 201):
                        self.location_ids.append(resp.json()["id"])
                        resp.success()
                    else:
                        resp.failure(f"Create location: {resp.status_code}")

    def _create_roles(self):
        chosen = random.sample(ROLE_NAMES, k=random.randint(3, 6))
        for name in chosen:
            with self.client.post(
                "/api/v1/roles/",
                json={"name": name, "description": f"{name} role"},
                headers=self.headers,
                name="/api/v1/roles/ [create]",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201):
                    self.role_ids.append(resp.json()["id"])
                    self.role_names_list.append(name)
                    resp.success()
                else:
                    resp.failure(f"Create role: {resp.status_code}")

    def _bulk_upload_employees(self):
        """Upload 10-50 employees per location via CSV bulk-upload."""
        if not self.location_ids or not self.role_names_list:
            return

        for loc_id in self.location_ids:
            emp_count = random.randint(10, 50)
            rows = ["full_name,email,role_names,skill_levels,location_names"]

            # We need the location name — fetch it
            with self.client.get(
                "/api/v1/locations/",
                headers=self.headers,
                name="/api/v1/locations/ [list]",
                catch_response=True,
            ) as resp:
                if resp.status_code != 200:
                    resp.failure(f"List locations: {resp.status_code}")
                    continue
                locations = resp.json()
                resp.success()

            loc_name = next(
                (loc["name"] for loc in locations if loc["id"] == loc_id),
                None,
            )
            if not loc_name:
                continue

            for _ in range(emp_count):
                # Assign 1-2 random roles
                num_roles = random.randint(1, min(2, len(self.role_names_list)))
                emp_roles = random.sample(self.role_names_list, num_roles)
                emp_skills = [str(random.randint(1, 5)) for _ in emp_roles]

                rows.append(
                    f"{fake.name()},{random_email()},"
                    f"{'|'.join(emp_roles)},{'|'.join(emp_skills)},{loc_name}"
                )

            csv_content = "\n".join(rows)

            with self.client.post(
                "/api/v1/employees/bulk-upload",
                files={"file": ("employees.csv", csv_content, "text/csv")},
                headers={"Authorization": f"Bearer {self.token}"},
                name="/api/v1/employees/bulk-upload",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201):
                    resp.success()
                else:
                    resp.failure(f"Bulk upload: {resp.status_code} {resp.text[:200]}")

        # Fetch all employee IDs for availability
        with self.client.get(
            "/api/v1/employees/",
            headers=self.headers,
            name="/api/v1/employees/ [list]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                self.employee_ids = [e["id"] for e in resp.json()]
                resp.success()
            else:
                resp.failure(f"List employees: {resp.status_code}")

    def _set_availability(self):
        """Set Mon-Fri 9-17 availability for each employee for current week."""
        if not self.employee_ids:
            return

        monday = current_week_monday()

        for emp_id in self.employee_ids:
            for day_offset in range(5):  # Mon-Fri
                day = monday + timedelta(days=day_offset)
                start = datetime(day.year, day.month, day.day, 9, 0)
                end = datetime(day.year, day.month, day.day, 17, 0)

                with self.client.post(
                    "/api/v1/employees/availability",
                    json={
                        "employee_id": emp_id,
                        "year": day.year,
                        "month": day.month,
                        "day": day.day,
                        "start_time": start.isoformat(),
                        "end_time": end.isoformat(),
                    },
                    headers=self.headers,
                    name="/api/v1/employees/availability [create]",
                    catch_response=True,
                ) as resp:
                    if resp.status_code in (200, 201):
                        resp.success()
                    else:
                        # Duplicates or conflicts are acceptable
                        if resp.status_code == 409:
                            resp.success()
                        else:
                            resp.failure(f"Availability: {resp.status_code}")

    def _create_shift_templates(self):
        """Create one shift template per location with random role/headcount combos."""
        if not self.location_ids or not self.role_ids:
            return

        for loc_id in self.location_ids:
            weekly_schedule = []
            # Pick 2-4 roles for this location's template
            template_roles = random.sample(
                list(zip(self.role_ids, self.role_names_list)),
                k=min(random.randint(2, 4), len(self.role_ids)),
            )

            num_days = random.randint(5, 7)
            for day in DAYS[:num_days]:
                for role_id, role_name in template_roles:
                    # Morning shift
                    weekly_schedule.append({
                        "day": day,
                        "role_id": role_id,
                        "role_name": role_name,
                        "headcount": random.randint(1, 4),
                        "start_time": "09:00",
                        "end_time": "17:00",
                    })
                    # Sometimes add an evening shift
                    if random.random() < 0.3:
                        weekly_schedule.append({
                            "day": day,
                            "role_id": role_id,
                            "role_name": role_name,
                            "headcount": random.randint(1, 2),
                            "start_time": "17:00",
                            "end_time": "23:00",
                        })

            with self.client.post(
                "/api/v1/shift-templates/",
                json={
                    "location_id": loc_id,
                    "name": f"{fake.color_name()} Shift Plan",
                    "weekly_schedule": weekly_schedule,
                },
                headers=self.headers,
                name="/api/v1/shift-templates/ [create]",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 201):
                    self.template_ids.append(resp.json()["id"])
                    resp.success()
                else:
                    resp.failure(f"Create template: {resp.status_code} {resp.text[:200]}")

    # ── Recurring tasks (run in loop after setup) ────────────────────────

    @task(10)
    def generate_schedule(self):
        """Generate a schedule for a random location using the local scheduler."""
        if not self.location_ids:
            return

        loc_id = random.choice(self.location_ids)
        template_id = self.template_ids[0] if self.template_ids else None
        monday = current_week_monday()

        body: dict = {
            "week_start_date": monday.isoformat(),
            "location_ids": [loc_id],
            "use_local": True,
            "strategy": random.choice(STRATEGIES),
            "strategy_param": round(random.uniform(0.1, 1.0), 2),
            "num_days": random.choice([5, 7]),
        }
        if template_id:
            body["template_ids"] = [template_id]

        with self.client.post(
            "/api/v1/schedules/generate",
            json=body,
            headers=self.headers,
            name="/api/v1/schedules/generate",
            catch_response=True,
            stream=True,
        ) as resp:
            if resp.status_code == 200:
                # Consume the NDJSON stream
                schedule_ids = []
                for line in resp.iter_lines():
                    if line:
                        try:
                            data = json.loads(line)
                            if "schedule_id" in data:
                                schedule_ids.append(data["schedule_id"])
                        except json.JSONDecodeError:
                            pass
                resp.success()

                # Approve or reject the generated schedules
                for sid in schedule_ids:
                    if random.random() < 0.7:
                        self._approve_schedule(sid)
                    else:
                        self._reject_schedule(sid)
            else:
                resp.failure(f"Generate: {resp.status_code} {resp.text[:200]}")

    @task(3)
    def list_employees(self):
        """Simulate viewing the employee list."""
        with self.client.get(
            "/api/v1/employees/",
            headers=self.headers,
            name="/api/v1/employees/ [list]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"List employees: {resp.status_code}")

    @task(3)
    def list_schedules(self):
        """Simulate viewing schedules for the current week."""
        monday = current_week_monday()
        with self.client.get(
            f"/api/v1/schedules/week/{monday.isoformat()}",
            headers=self.headers,
            name="/api/v1/schedules/week/[date]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"List schedules: {resp.status_code}")

    @task(2)
    def view_availability(self):
        """Simulate viewing all employee availability."""
        monday = current_week_monday()
        with self.client.get(
            f"/api/v1/employees/availability/all?week_start={monday.isoformat()}",
            headers=self.headers,
            name="/api/v1/employees/availability/all",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"View availability: {resp.status_code}")

    @task(2)
    def list_shift_templates(self):
        """Simulate viewing shift templates."""
        with self.client.get(
            "/api/v1/shift-templates/",
            headers=self.headers,
            name="/api/v1/shift-templates/ [list]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"List templates: {resp.status_code}")

    @task(1)
    def view_dashboard(self):
        """Simulate loading the dashboard (company info + billing)."""
        with self.client.get(
            "/api/v1/company/",
            headers=self.headers,
            name="/api/v1/company/ [get]",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Company: {resp.status_code}")

        with self.client.get(
            "/api/v1/billing/usage",
            headers=self.headers,
            name="/api/v1/billing/usage",
            catch_response=True,
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Billing: {resp.status_code}")

    # ── Schedule approval/rejection helpers ──────────────────────────────

    def _approve_schedule(self, schedule_id: str):
        with self.client.post(
            f"/api/v1/schedules/{schedule_id}/approve",
            headers=self.headers,
            name="/api/v1/schedules/[id]/approve",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"Approve: {resp.status_code}")

    def _reject_schedule(self, schedule_id: str):
        with self.client.post(
            f"/api/v1/schedules/{schedule_id}/reject",
            headers=self.headers,
            name="/api/v1/schedules/[id]/reject",
            catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"Reject: {resp.status_code}")
