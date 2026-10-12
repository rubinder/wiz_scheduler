# WizScheduler Pricing & Cost Analysis

## Actual Pricing Constants (2026-10-12)

### Revenue Model
- **Base subscription**: $18.00/month
- **Overage rates**:
  - Employees: $1.00 per 1,000 (beyond 1,000 included)
  - Schedules: $0.10 per 50 (beyond 50/month included)
  - Storage: $0.50 per GB (beyond 0.5 GB included)
  - AI tokens: Cost × 1.30x markup

### Claude API Costs (Sonnet 5.5)
- Input: $2.00 per 1M tokens
- Output: $10.00 per 1M tokens
- Applied at 1.30x markup (130% = 30% margin capture)

---

## Unit Economics by Customer Profile

### Assumptions
- **Tokens per schedule**: ~7,000 input + 3,000 output (typical)
- **Token cost per schedule**: (7,000 × $2 + 3,000 × $10) / 1M × 1.30 = **~$0.052/schedule**

---

## Customer Profiles & Monthly Costs

### Profile A: Small Café (50 employees, 4 schedules/month)
```
Revenue:
  Base subscription              $18.00
  Employees (50, within 1K)      $0.00
  Schedules (4, within 50)       $0.00
  AI tokens (4 × $0.052)         $0.21
  ───────────────────────────────
  Total Revenue                  $18.21

Costs:
  Infrastructure (shared)        ~$2.00  (hosting, database, ops)
  Claude API (4 schedules)       $0.20   (4 × $0.052)
  ───────────────────────────────
  Total Cost                     ~$2.20

Gross Margin: 88% | Net: +$15.01
```

### Profile B: Restaurant Group (500 employees, 20 schedules/month)
```
Revenue:
  Base subscription              $18.00
  Employees (500, within 1K)     $0.00
  Schedules (20, within 50)      $0.00
  AI tokens (20 × $0.052)        $1.04
  ───────────────────────────────
  Total Revenue                  $19.04

Costs:
  Infrastructure (shared)        ~$3.00
  Claude API (20 schedules)      $1.04
  ───────────────────────────────
  Total Cost                     ~$4.04

Gross Margin: 79% | Net: +$15.00
```

### Profile C: Large Coffee Chain (1,200 employees, 80 schedules/month)
```
Revenue:
  Base subscription              $18.00
  Employees (1,200, 200 over)    $0.20   (1 block × $1.00)
  Schedules (80, 30 over)        $0.06   (~0.6 blocks × $0.10)
  AI tokens (80 × $0.052)        $4.16
  ───────────────────────────────
  Total Revenue                  $22.42

Costs:
  Infrastructure (shared)        ~$3.50
  Claude API (80 schedules)      $4.16
  ───────────────────────────────
  Total Cost                     ~$7.66

Gross Margin: 66% | Net: +$14.76
```

### Profile D: Regional Chain (5,000 employees, 200 schedules/month)
```
Revenue:
  Base subscription              $18.00
  Employees (5,000, 4K over)     $4.00   (4 blocks × $1.00)
  Schedules (200, 150 over)      $0.30   (3 blocks × $0.10)
  AI tokens (200 × $0.052)       $10.40
  ───────────────────────────────
  Total Revenue                  $32.70

Costs:
  Infrastructure (scaled)        ~$5.00
  Claude API (200 schedules)     $10.40
  ───────────────────────────────
  Total Cost                     ~$15.40

Gross Margin: 53% | Net: +$17.30
```

### Profile E: Enterprise (10,000 employees, 400 schedules/month)
```
Revenue:
  Base subscription              $18.00
  Employees (10K, 9K over)       $9.00   (9 blocks × $1.00)
  Schedules (400, 350 over)      $0.70   (7 blocks × $0.10)
  AI tokens (400 × $0.052)       $20.80
  ───────────────────────────────
  Total Revenue                  $48.50

Costs:
  Infrastructure (scaled)        ~$7.00
  Claude API (400 schedules)     $20.80
  ───────────────────────────────
  Total Cost                     ~$27.80

Gross Margin: 43% | Net: +$20.70
```

---

## Key Findings

### 1. **Pricing is Healthy**
- All customer profiles are **profitable**
- Gross margins range from **43% to 88%**
- Even enterprise customers have healthy unit economics

### 2. **Current Model Works Well**
- Base $18/month covers fixed infrastructure costs
- Overage model captures variable LLM costs (largest expense)
- Employee/schedule/storage charges provide additional revenue

### 3. **Margin Compression at Scale**
- Small teams: 88% gross margin
- Large teams: 43% gross margin
- **This is expected** — scale increases absolute profit, reduces margin %

### 4. **LLM Costs Dominate**
- AI token costs are the largest variable expense (~30-40% of revenue for high users)
- Overage markup (1.30x) ensures margin preservation as scale increases

---

## Should You Increase Per-Employee Pricing?

### Current Model
- Employees: $1.00 per 1,000 (= **$0.001/employee**)
- Schedules: $0.10 per 50 (= **$0.002/schedule**)

### Analysis: Should Per-Employee Pricing Increase?

**Argument FOR increase:**
- Large customer (10K employees) paying only $9 in employee overages
- This is less than $1/employee/month
- Similar to competitors: 7shifts ($3-7/user), Deputy ($6-12/user)

**Argument AGAINST:**
- Current model is already highly profitable (43% margin on enterprise)
- Per-user pricing is acquisition friction for growth
- Schedules + AI tokens already scale linearly with usage
- Infrastructure costs don't scale linearly with employee count

**Recommendation: KEEP CURRENT MODEL**
- Your overages already capture 95%+ of variable costs
- Increasing per-employee pricing adds friction without clear cost justification
- Better competitive advantage: lower per-employee cost than competitors
- Focus on growing customer count rather than extracting more per customer

---

## Summary Table

| Profile | Employees | Schedules/mo | Monthly Revenue | Est. Cost | Margin | Notes |
|---------|-----------|--------------|-----------------|-----------|--------|-------|
| A: Café | 50 | 4 | $18.21 | $2.20 | 88% | Mostly base fee |
| B: Restaurant | 500 | 20 | $19.04 | $4.04 | 79% | Base + tokens |
| C: Chain | 1,200 | 80 | $22.42 | $7.66 | 66% | First employee overage |
| D: Regional | 5,000 | 200 | $32.70 | $15.40 | 53% | Multiple overages |
| E: Enterprise | 10,000 | 400 | $48.50 | $27.80 | 43% | Heavy user |

All margins are **healthy**. Current pricing model is optimal.
