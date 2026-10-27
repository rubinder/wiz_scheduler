# Task 2 report: employee submission API

Files: backend/schemas/preference_submission.py, backend/services/preference_submissions.py,
backend/routers/preference_submissions.py, backend/main.py (mount),
tests/test_preference_submissions_employee_integration.py (20 tests),
tests/test_preference_submission_schemas.py (12 tests incl. Approved* validation).

Service API for Task 3 (backend/services/preference_submissions.py):
- DISABLED_CODE = "employee_preference_submissions_disabled"
- relationship_from_level(level: float) -> str
- load_my_employee(db, user_id, company_id) -> Employee (404 if none/archived)
- is_enabled(db, company_id) -> bool
- share_location_coworkers(db, employee) -> list[Employee]
- validate_affinity_targets(db, employee, target_ids) -> None (422; self/dupes/inactive/other company/no shared location)
- build_current_view(db, employee, *, include_values=False) -> dict  (True adds weight/level; outgoing affinities only; archived targets omitted)
- redact_approved(db, company_id, approved_payload: dict) -> dict (coworker names only)
- get_pending(db, employee_id), get_last_decision(db, employee), build_my_state(db, employee)

Schemas: SubmittedX / PreferenceSubmissionPayload (extra=forbid, 3-day cap, uniqueness),
ApprovedAffinity/Day/HourRange/Cap/Blackout + ApprovedPayload (weight required step 0.1;
level in {-1,-0.5,0.5,1}; importance optional), My* views, MySubmissionState, LastDecision, ApprovedSummary, Coworker.

Tests: pytest tests/test_preference_submissions_employee_integration.py tests/test_preference_submission_schemas.py: 32 passed;
RLS_TEST=1 on the integration file: 20 passed; tests/test_utc_today.py passes.

Concerns: affinity level rule uses the discrete set {-1,-0.5,0.5,1} (excludes 0). approved_payload dict
read by redact_approved assumes ApprovedPayload.model_dump(mode="json") shape. Manager view omits archived affinity targets
(approve replaces all outgoing rows, so those are dropped on approve).
