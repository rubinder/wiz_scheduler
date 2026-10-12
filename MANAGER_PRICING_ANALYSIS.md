# Manager Pricing Analysis: First 50 Managers Included, Then $5 per 50

## Proposed Model
- **First 50 managers included** in $18/month base
- **$5 per 50 managers** thereafter
- Assumption: 2-3 managers per location

---

## Competitive Comparison: 7shifts vs WizScheduler

### Scenario A: Single Location, 100 Employees, 2 Managers

**7shifts**
```
$39.99 per location
Total: $39.99/month
```

**WizScheduler (Current)**
```
Base:      $18.00
Employees: $0.00
Schedules: $0.00
Storage:   $0.00
───────────────────
Total:     $18.00/month
```

**WizScheduler (With Manager Pricing)**
```
Base:      $18.00
Employees: $0.00
Schedules: $0.00
Storage:   $0.00
Managers:  $0 (2 managers within 50 included)
───────────────────
Total:     $18.00/month
```

**Winner: You** (2.2x cheaper, NO manager charge)

---

### Scenario B: 5 Locations, 500 Employees, 10-15 Managers

**7shifts**
```
$39.99 × 5 = $199.95/month
```

**WizScheduler (Current)**
```
Base:      $18.00
Employees: $0.00
───────────────────
Total:     $18.00/month
```

**WizScheduler (With Manager Pricing)**
```
Base:        $18.00
Employees:   $0.00
Managers:    $0 (10-15 managers within 50 included)
───────────────────
Total:       $18.00/month
```

**Winner: You** (11.1x cheaper, NO manager charge)

---

### Scenario C: 10 Locations, 2,500 Employees, 20-30 Managers

**7shifts**
```
$39.99 × 10 = $399.90/month
```

**WizScheduler (Current)**
```
Base:        $18.00
Employees:   $4.00
Schedules:   $0.30
Storage:     $0.00
───────────────────
Total:       $22.30/month
```

**WizScheduler (With Manager Pricing)**
```
Base:        $18.00
Employees:   $4.00
Schedules:   $0.30
Managers:    $0 (20-30 managers within 50 included)
───────────────────
Total:       $22.30/month
```

**Winner: You** (17.9x cheaper, NO manager charge)

---

### Scenario D: 20 Locations, 10,000 Employees, 40-60 Managers

**7shifts**
```
$39.99 × 20 = $799.80/month
```

**WizScheduler (Current)**
```
Base:        $18.00
Employees:   $19.00
Schedules:   $1.50
Storage:     $2.25
───────────────────
Total:       $40.75/month
```

**WizScheduler (With Manager Pricing) — 50 Managers (at threshold)**
```
Base:        $18.00
Employees:   $19.00
Schedules:   $1.50
Storage:     $2.25
Managers:    $0 (50 managers = exactly at 50 included)
───────────────────
Total:       $40.75/month
```

**Winner: You** (19.6x cheaper, NO manager charge)

**WizScheduler (With Manager Pricing) — 60 Managers (over threshold)**
```
Base:        $18.00
Employees:   $19.00
Schedules:   $1.50
Storage:     $2.25
Managers:    $5 (10 over 50 included)
───────────────────
Total:       $45.75/month
```

**Winner: You** (17.5x cheaper even with manager charge)

---

## Verdict: NO, Absolutely Not More Expensive

With **first 50 managers included** + **$5 per 50 thereafter**, you remain **11-20x cheaper** than 7shifts.

**Key insight:** Most teams (< 50 managers = ~17-25 locations) pay ZERO for managers.

---

## Strategic Considerations

### ✅ Pros of Adding Manager Pricing
1. **Captures value** from larger management teams
2. **Aligns revenue** with actual usage (managers drive scheduling decisions)
3. **Additional MRR** with minimal friction
4. **Defensible** — managers are power users, reasonable to charge for them

### ❌ Cons of Adding Manager Pricing
1. **Complexity** — pricing goes from 3 dimensions (base + employees + schedules) to 4
2. **Marketing friction** — harder to explain "manager" as a metered dimension
3. **Conversion risk** — small teams (1-2 managers) might skip free trial
4. **Competitive perception** — 7shifts doesn't charge per manager, so it looks like we do

---

## Alternative: Tiered Manager Pricing

Instead of linear per-50-managers, consider tiers:

```
Free Plan:
  Up to 2 managers

Pro Plan: $18/month
  Up to 5 managers included
  $1 per additional manager tier (5-10, 10-20, etc.)
```

This way:
- Most small teams (2-5 managers) see no additional charge
- Growing teams (10+ managers) pay proportionally
- Simpler to communicate: "manager seats included"

---

## Recommendation

## Recommendation: YES, Add Manager Pricing

This model is **excellent**:

✅ **First 50 managers free** = covers almost all SMBs (2-25 locations)
✅ **$5 per 50 after** = high barrier, only enterprise encounters it
✅ **11-20x cheaper than 7shifts** across all customer sizes
✅ **Simple to explain**: "50 manager seats included in $18"
✅ **Captures enterprise value** without hurting SMB conversion
✅ **Still lands on simplicity**: 4 dimensions (base + employees + schedules + managers)

### Why This Works

| Team Size | Locations | Managers | Manager Cost | Total Pro Cost | 7shifts Total | Your Win |
|-----------|-----------|----------|--------------|----------------|---------------|----------|
| Micro | 1 | 2 | $0 | $18 | $40 | 2.2x cheaper |
| Small | 5 | 10 | $0 | $18 | $200 | 11x cheaper |
| Medium | 10 | 20 | $0 | $22.30 | $400 | 18x cheaper |
| Large | 20 | 50 | $0 | $40.75 | $800 | 19.6x cheaper |
| **Enterprise** | **30** | **75** | **$5** | ~$48 | $1,200 | **25x cheaper** |
| **Mega** | **50** | **150** | **$10** | ~$60 | $2,000 | **33x cheaper** |

---

## Pricing Summary

```
Pro Plan: $18/month

Included:
  ✓ 500 employees
  ✓ 25 schedule generations/month
  ✓ 50 manager seats
  ✓ 0.5 GB storage

Overages:
  + $1 per 500 employees
  + $0.10 per 25 schedules
  + $5 per 50 managers
  + $0.50 per GB storage
```

**This is compelling, simple, and crushes 7shifts on every segment.**

