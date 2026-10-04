# Phase 1: Scheduling Signal Controls Logic Implementation

## Overview

Integrate signal weight parameters into the scheduling pipeline so they are:
1. Passed through the scheduling state
2. Applied in scoring functions
3. Saved with the generated schedule for audit trail

## Files to Modify

### 1. `backend/scheduling/state.py` — Add signal_config to SchedulingState

```python
# In SchedulingState, add:
signal_config: Dict[str, float] = {
    "seniority_weight": 0.0,
    "pay_weight": 0.0,
    "overtime_weight": 0.0,
    "affinity_weight": 0.0,
}
```

### 2. `backend/scheduling/graph.py` — Pass signal weights to graph

**Update `build_scheduling_graph` signature:**
```python
def build_scheduling_graph(
    use_local: bool = False,
    strategy: Strategy = "random",
    strategy_param: float = 0.5,
    strategy_param2: float = 0.0,
    seniority_weight: float = 0.0,
    pay_weight: float = 0.0,
    overtime_weight: float = 0.0,
    affinity_weight: float = 0.0,
) -> StateGraph:
```

**Update `run_scheduling_pipeline` call signature** (lower in file):
```python
async def run_scheduling_pipeline(
    db: AsyncSession,
    company_id: str,
    location_ids: list[str],
    week_start_date: date_type,
    use_local: bool = False,
    strategy: Strategy = "random",
    strategy_param: float = 0.5,
    strategy_param2: float = 0.0,
    seniority_weight: float = 0.0,
    pay_weight: float = 0.0,
    overtime_weight: float = 0.0,
    affinity_weight: float = 0.0,
) -> AsyncGenerator[LocationResult, None]:
```

**Initialize state with signal_config:**
```python
state = {
    "locations": locations,
    "current_location_index": 0,
    "employees": employees,
    "availability_draft": availability_draft,
    "signal_config": {
        "seniority_weight": seniority_weight,
        "pay_weight": pay_weight,
        "overtime_weight": overtime_weight,
        "affinity_weight": affinity_weight,
    },
    # ... other fields
}
```

### 3. `backend/scheduling/local_scheduler.py` — Use signal weights in scoring

**Update `local_schedule` signature:**
```python
def local_schedule(
    state: SchedulingState,
    strategy: Strategy = "random",
    strategy_param: float = 0.5,
    strategy_param2: float = 0.0,
) -> Dict[str, Any]:
```

**In employee scoring section**, modify the score calculation:
```python
signal_config = state.get("signal_config", {})
seniority_weight = signal_config.get("seniority_weight", 0.0)
pay_weight = signal_config.get("pay_weight", 0.0)
overtime_weight = signal_config.get("overtime_weight", 0.0)
affinity_weight = signal_config.get("affinity_weight", 0.0)

# When calculating candidate score:
base_score = ...  # existing logic

# Apply weighted signals
if seniority_weight > 0:
    seniority_adjustment = seniority_score(...) * seniority_weight
    base_score += seniority_adjustment

if pay_weight > 0:
    pay_adjustment = pay_score(...) * pay_weight
    base_score += pay_adjustment

if overtime_weight > 0:
    overtime_adjustment = overtime_score(...) * overtime_weight
    base_score += overtime_adjustment

if affinity_weight > 0:
    affinity_adjustment = affinity_score(...) * affinity_weight
    base_score += affinity_adjustment
```

### 4. `backend/scheduling/cost_seniority.py` — Implement signal-aware scoring

**Add weight-aware wrapper functions:**
```python
def seniority_score_weighted(emp: Dict[str, Any], weight: float) -> float:
    """Weighted seniority score (0-1 scale, weighted by weight parameter)."""
    if weight <= 0:
        return 0.0
    raw_score = seniority_score(emp, resolved_ranks)
    return raw_score * weight

def pay_score_weighted(emp: Dict[str, Any], pool: List[Dict], weight: float) -> float:
    """Weighted pay score (lower wage = better, weighted by weight parameter)."""
    if weight <= 0:
        return 0.0
    # Invert pay (lower = better for cost optimization)
    max_pay = max(float(e.get("pay_rate", 0)) for e in pool if e.get("pay_rate"))
    emp_pay = float(emp.get("pay_rate", max_pay))
    raw_score = 1.0 - (emp_pay / max_pay)
    return raw_score * weight

def overtime_score_weighted(emp: Dict, hours_sum: float, threshold: float, weight: float) -> float:
    """Weighted overtime penalty (minimize overtime, weighted by weight parameter)."""
    if weight <= 0:
        return 0.0
    if hours_sum < threshold:
        return 0.0  # No penalty if under threshold
    hours_over = hours_sum - threshold
    penalty = hours_over / threshold  # Normalized penalty
    return -(penalty * weight)  # Negative adjustment (penalty)
```

### 5. `backend/routers/schedules.py` — Extract and save signal_config

**Update the generate_schedule endpoint:**
```python
# Extract signal weights from request
signal_config = {
    "seniority_weight": body.seniority_weight,
    "pay_weight": body.pay_weight,
    "overtime_weight": body.overtime_weight,
    "affinity_weight": body.affinity_weight,
    "strategy": body.strategy,
    "strategy_param": body.strategy_param,
    "strategy_param2": body.strategy_param2,
}

# Pass to pipeline
async for location_result in run_scheduling_pipeline(
    db,
    str(current_user.company_id),
    location_ids,
    body.week_start_date,
    use_local=body.use_local,
    strategy=body.strategy,
    strategy_param=body.strategy_param or 0.5,
    strategy_param2=body.strategy_param2 or 0.0,
    seniority_weight=body.seniority_weight,
    pay_weight=body.pay_weight,
    overtime_weight=body.overtime_weight,
    affinity_weight=body.affinity_weight,
):
    # ...
```

**When creating ShiftSchedule:**
```python
shift_schedule = ShiftSchedule(
    id=ss_id,
    company_id=company_id,
    location_id=location_result.location_id,
    week_start_date=body.week_start_date,
    status="draft",
    strategy=body.strategy,
    strategy_param=body.strategy_param,
    strategy_param2=body.strategy_param2,
    signal_config=signal_config,  # ← Save it
    # ... other fields
)
```

## Testing Strategy

1. **Unit tests** for signal scoring functions (`test_cost_seniority.py`)
   - Test seniority scoring with weight=0, 0.5, 1.0
   - Test pay scoring with weight variations
   - Test overtime penalty logic

2. **Integration tests** (Playwright)
   - Generate schedule with seniority_weight=1.0, others=0.0
   - Verify senior employees are preferred
   - Check signal_config is saved in response

3. **Backend tests** with seed_integration_test.py data
   - Run scheduling with different weight combinations
   - Verify schedule quality metrics change appropriately

## Notes

- Default weights are 0.0 (signals disabled) for backward compatibility
- Signal scoring is additive — weights are applied independently
- Overtime weight has special logic (penalty-based, not additive)
- All weights normalize to [0, 1] range with Field validation in schema

## Next Phase

Once Phase 1 is complete:
- Phase 2: Frontend settings (company/location defaults)
- Phase 3: UI generation dialog + review display

---

**Estimated effort**: 2-3 hours for implementation + testing
**Priority**: Start with signal_config state flow, then add scoring logic
