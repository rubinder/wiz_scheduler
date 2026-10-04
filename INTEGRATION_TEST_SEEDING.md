# Integration Test Seeding

This document explains how to seed the production database with test data for integration testing.

## Overview

`seed_integration_test.py` creates an idempotent test company with:
- **55 employees** (configurable) across 2 locations
- **40 employees** assigned to both locations (to test cross-location scheduling constraints)
- **Normally distributed signals**: pay rates, seniority ranks, random affinities
- **30 days** of 9–5 availability (configurable, refreshed before each test)
- **Non-AI scheduling**: Use deterministic strategies only, no Claude calls

## Setup

### AWS Parameter Store

Store RDS credentials at these paths:
```
/wizscheduler/test-db/host
/wizscheduler/test-db/port
/wizscheduler/test-db/username
/wizscheduler/test-db/password
/wizscheduler/test-db/database
```

Example (AWS CLI):
```bash
aws ssm put-parameter --name /wizscheduler/test-db/host --value "prod-db.example.com" --type String
aws ssm put-parameter --name /wizscheduler/test-db/port --value "5432" --type String
aws ssm put-parameter --name /wizscheduler/test-db/username --value "admin" --type String
aws ssm put-parameter --name /wizscheduler/test-db/password --value "secret" --type SecureString
aws ssm put-parameter --name /wizscheduler/test-db/database --value "wizscheduler" --type String
```

### Lambda Function

1. **Create function** with Python 3.11 runtime
2. **Deploy code**: Upload `seed_integration_test.py` + `requirements_seed.txt`
3. **Handler**: `seed_integration_test.lambda_handler`
4. **VPC**: Configure with RDS security group and subnet
5. **IAM role**: Attach `AmazonSSMReadOnlyAccess` for Parameter Store reads
6. **Environment variables** (optional):
   - `DB_PARAM_PATH`: Parameter Store path prefix (default: `/wizscheduler/test-db`)
   - `TEST_COMPANY_ID`: Test company ID (default: `integ-test-001`)
   - `EMPLOYEE_COUNT`: Number of employees (default: `55`)
   - `AVAILABILITY_DAYS`: Days of availability (default: `30`)

### CloudShell (Manual Testing)

```bash
# Clone and prepare
git clone <repo>
cd wiz_scheduler

# Install dependencies
pip install -r requirements_seed.txt

# Run seeding
export AWS_REGION=us-east-1
export DB_PARAM_PATH=/wizscheduler/test-db
python seed_integration_test.py
```

## Usage

### Lambda Invocation

```bash
aws lambda invoke \
  --function-name seed-integration-test \
  --region us-east-1 \
  response.json

cat response.json
```

Returns:
```json
{
  "statusCode": 200,
  "body": {
    "company_id": "integ-test-001",
    "location_1_id": "integ-loc-001",
    "location_2_id": "integ-loc-002",
    "employee_count": 55,
    "both_locations_count": 40
  }
}
```

### Idempotency

The script is safe to re-run. It will:
- Create company/locations/roles if missing
- Update employee attributes if they exist
- **Refresh** availability windows (delete old, insert fresh)

This allows running the same seeding before each test cycle to ensure consistent availability data.

## Integration Test Assertions

After seeding, integration tests should verify:

1. **Cross-location scheduling**: Generate schedule for both locations
   - Verify no employee is assigned to work at Location 1 and Location 2 simultaneously
   - Assert shifts don't overlap in time across locations

2. **Scheduling signals**: Test with different strategies
   - Verify seniority ranks influence lead assignments (high seniority → preferred for Team Lead)
   - Verify pay rates are considered in cost optimization
   - Verify affinities influence team composition

3. **Non-AI scheduling only**: Confirm scheduling method
   - Use `strategy` parameter (e.g., `greedy`, `balanced`)
   - Verify `raw_llm_output` is NULL (no AI involved)

## Maintenance

When schema changes occur:

1. **Add new fields** to seeding logic
   - Update employee creation with new columns
   - Add generators for signal distributions if needed

2. **Update availability window**:
   - Adjust `AVAILABILITY_DAYS` or modify `start_time`/`end_time`

3. **Adjust employee distribution**:
   - Change `BOTH_LOCATIONS_COUNT` to test different overlap scenarios
   - Modify affinity generation ratio (currently 30% of pairs)

## Example Integration Test

```python
# tests/test_scheduling_signals.py
import pytest
from httpx import AsyncClient

@pytest.mark.asyncio
async def test_cross_location_no_overlap(client: AsyncClient):
    """Verify no employee scheduled at both locations simultaneously."""
    company_id = "integ-test-001"  # From seeding
    location_1 = "integ-loc-001"
    location_2 = "integ-loc-002"
    week_start = "2026-10-06"
    
    # Generate schedules for both locations
    resp = await client.post(
        "/api/v1/schedules/generate",
        json={
            "company_id": company_id,
            "location_ids": [location_1, location_2],
            "week_start_date": week_start,
            "strategy": "balanced",  # Non-AI
        },
    )
    assert resp.status_code == 200
    
    # Verify no overlaps
    result = resp.json()
    shifts_by_employee = {}
    for shift in result["shifts"]:
        emp_id = shift["employee_id"]
        if emp_id not in shifts_by_employee:
            shifts_by_employee[emp_id] = []
        shifts_by_employee[emp_id].append(shift)
    
    for emp_id, shifts in shifts_by_employee.items():
        locations = {s["location_id"] for s in shifts}
        assert len(locations) <= 1, f"Employee {emp_id} scheduled at multiple locations"
```

---

**Last updated**: 2026-10-03
