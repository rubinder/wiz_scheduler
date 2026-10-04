# Scheduling Signal Controls Feature

User-configurable signal weighting for schedule generation strategies.

## Requirements

### 1. Signal Weights (0-1 Scale, 0.1 Increments)

Each strategy accepts signal weight parameters (0.0 = ignored, 1.0 = heavily weighted):

```python
class GenerateRequest(BaseModel):
    strategy: Literal["random", "rotation", "rotation_history", "max_hours"]
    
    # Signal weights: 0.0–1.0, increments of 0.1 (default: 0.0 = not relevant)
    seniority_weight: float = 0.0       # 0=ignore, 1=strongly favor senior employees
    pay_weight: float = 0.0             # 0=ignore, 1=strongly favor lower-wage employees
    overtime_weight: float = 0.0        # 0=ignore, 1=aggressively minimize overtime
    affinity_weight: float = 0.0        # 0=ignore, 1=strongly apply team preferences
    
    # Strategy params (existing)
    strategy_param: float | None = None
    strategy_param2: float | None = None
```

**Signal Semantics:**
- `seniority_weight=1.0`: Prioritize most senior employees (higher seniority_rank)
- `pay_weight=1.0`: Prioritize lowest-wage employees (lower pay_rate = cost optimization)
- `overtime_weight=1.0`: Minimize overtime via iterative checks (see algorithm below)
- `affinity_weight=1.0`: Apply full team composition preferences

### 1a. Overtime Threshold

Hours per week an employee needs to work to be eligible for overtime (and thus subject to overtime weighting).

**Hierarchy (used in algorithm):**
1. Location `overtime_threshold_hours` (if set)
2. Company `overtime_threshold_hours` (if set)
3. Hard default: 40 hours/week

**Typical values:**
- US standard: 40 hours/week
- EU standard: 35–37 hours/week
- Healthcare/retail: 35 hours/week
- Part-time workforce: 24 hours/week

### 1b. Overtime Minimization Algorithm

When `overtime_weight > 0`, the scheduler applies an iterative check using the overtime threshold:

```
For each candidate employee assignment:
  1. Calculate employee's current hours this week (from already-scheduled shifts)
  2. Get overtime_threshold_hours for location (company default or location override)
  3. If (current_hours + shift_duration) > threshold:
     - If overtime_weight == 1.0: Skip this employee (hard constraint)
     - If 0 < overtime_weight < 1.0: Penalize score proportionally
       hours_over = (current_hours + shift_duration) - threshold
       score *= (1 - overtime_weight * (hours_over / threshold))
  4. If no eligible employees remain, fall back to candidates over threshold
```

**Example:**
- Company overtime_threshold: 40 hours/week
- Employee A: 38 hours scheduled, shift is 4 hours → would be 42h (2h over)
  - overtime_weight=1.0: Skip entirely
  - overtime_weight=0.5: Score *= (1 - 0.5 * (2/40)) = 0.975 (2.5% penalty)
  - overtime_weight=0.0: No penalty
- Employee B: 35 hours scheduled, shift is 4 hours → stays at 39h (under threshold)
  - All weights: Preferred (no overtime concern)

### 2. Configuration Hierarchy

**Company-level defaults** (can set defaults for all locations):
```
Settings → Scheduling Defaults

Overtime Rules:
  Threshold Hours/Week: [40] ✓  (when employee hours exceed this, overtime rules apply)
  
Signal Weights:
  Seniority Weight:   [====●====] 0.5
  Pay Weight:         [====●====] 0.5
  Overtime Weight:    [●========] 0.1
  Affinity Weight:    [========●] 0.8
```

**Location-level overrides** (can override per location):
```
Location Details → Scheduling Preferences

☐ Override company defaults

Overtime Rules:
  Threshold Hours/Week: [35] ✓  (location override: part-time focus)
  
Signal Weights:
  Seniority Weight:   [====●====] 0.5
  Pay Weight:         [====●====] 0.5
  Overtime Weight:    [●========] 0.1
  Affinity Weight:    [========●] 0.8
```

**Schedule generation** (can override both):
```
Generate Schedule dialog
  Strategy: [dropdown]
  
  Seniority Weight:   [====●====] 0.5  (0=ignore, 1=favor most senior)
  Pay Weight:         [====●====] 0.5  (0=ignore, 1=favor lowest wage)
  Overtime Weight:    [●========] 0.1  (0=ignore, 1=minimize overtime)
  Affinity Weight:    [========●] 0.8  (0=ignore, 1=maximize preferences)
  
  [Generate Schedule]
```

### 3. Save With Schedule

Store signal configuration in `shift_schedules` table with weighted values:

```sql
ALTER TABLE shift_schedules ADD COLUMN signal_config JSONB;

-- Example: Generated with moderate seniority preference, minimal overtime concern, strong affinity weighting
{
  "seniority_weight": 0.6,
  "pay_weight": 0.0,
  "overtime_weight": 0.1,
  "affinity_weight": 0.9,
  "strategy": "rotation",
  "strategy_param": null,
  "strategy_param2": null,
  "generated_at": "2026-10-06T14:30:00Z"
}
```

### 4. Display Signal Rationale

Show on schedule review page:

```
Schedule Generated: Oct 6, 2026 at 2:45 PM
Strategy: Rotation

Signal Weighting Applied:
  Seniority:  ▓▓▓▓▓░░░░░ 0.5  (moderate preference for senior staff)
  Pay Rate:   ░░░░░░░░░░ 0.0  (cost not considered)
  Overtime:   ▓░░░░░░░░░ 0.1  (minimal overtime concern)
  Affinities: ▓▓▓▓▓▓▓▓▓░ 0.9  (strong team preference weighting)

Summary: Schedule prioritizes team chemistry and seniority balance,
with minimal cost optimization and overtime minimization.
```

## Implementation Plan

### Backend Changes

1. **backend/schemas/schedule.py**
   - Add signal toggles to `GenerateRequest`
   - Store signal_config in schedule creation

2. **backend/scheduling/cost_seniority.py**
   - Accept signal config dict
   - Conditionally apply each signal based on toggle

3. **backend/scheduling/local_scheduler.py**
   - Pass signal config to scoring functions
   - Skip disabled signals

4. **backend/routers/schedules.py**
   - Extract signal config from request
   - Save to shift_schedules.signal_config (JSONB)

5. **Database Migration**
   - Add `signal_config` column to `shift_schedules`
   - Default: NULL (assume all enabled for backward compatibility)

### Frontend Changes

1. **Company Settings Page** (new section)
   - Scheduling signal defaults per company
   - Save to `companies.signal_config` (nullable)

2. **Location Details Page** (new section)
   - Override company defaults per location
   - Save to `locations.signal_config` (nullable)

3. **Schedule Generation Dialog**
   - Show signal toggles before generation
   - Inherit company/location defaults
   - Allow manual override

4. **Schedule Review Page**
   - Display "Generated With" signal summary
   - Show rationale for why shifts are assigned

## Database Schema

```sql
-- Overtime threshold (already exists in schema)
-- ALTER TABLE companies ADD COLUMN overtime_threshold_hours FLOAT;  -- already exists
-- ALTER TABLE locations ADD COLUMN overtime_threshold_hours FLOAT;  -- already exists

-- Signal weights configuration (new)
ALTER TABLE companies ADD COLUMN signal_config JSONB;  -- nullable
-- {
--   "seniority_weight": 0.5,
--   "pay_weight": 0.5,
--   "overtime_weight": 0.1,
--   "affinity_weight": 0.8
-- }

ALTER TABLE locations ADD COLUMN signal_config JSONB;  -- nullable
-- Same structure as above (overrides company defaults)

-- Schedule signal audit trail (new)
ALTER TABLE shift_schedules ADD COLUMN signal_config JSONB;  -- nullable
-- {
--   "seniority_weight": 0.5,
--   "pay_weight": 0.5,
--   "overtime_weight": 0.1,
--   "affinity_weight": 0.8,
--   "strategy": "rotation",
--   "generated_at": "2026-10-06T14:30:00Z"
-- }
```

**Note:** `overtime_threshold_hours` already exists on `companies` and `locations` tables (from PR #134). The signal controls feature reuses this existing column.

## Backward Compatibility

- Default behavior (all toggles false): use existing hardcoded behavior
- Existing schedules have `signal_config IS NULL`
- When NULL, assume signals are active (current behavior)

## Testing

1. **Integration tests**
   - Generate schedule with signal_config: {use_pay_rate: true}
   - Verify signal_config saved to database
   - Verify schedule reflects pay optimization

2. **Browser tests** (Playwright)
   - Configure company defaults
   - Override at location level
   - Generate schedule and verify signals applied
   - Check signal summary on review page

## Related Issues

- PR #135: Scheduling signals (merged)
- This feature: User control over signal weighting

---

**Priority**: Medium (nice-to-have, improves UX)
**Effort**: 2-3 PRs (backend signals API, frontend settings, frontend generation UI)
**Timeline**: Post-MVP, after integration tests are stable
