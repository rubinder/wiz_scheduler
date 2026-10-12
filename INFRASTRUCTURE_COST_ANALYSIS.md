# WizScheduler Infrastructure Cost Analysis
## Real Costs: RDS, Network, Compute (Excluding AI Tokens)

---

## Infrastructure Components & Scaling

### 1. **RDS PostgreSQL Costs**

#### Database Instance (Primary)
| Customer Size | Est. Connections | Current Instance | Monthly Cost |
|---------------|------------------|-----------------|--------------|
| Small (50 emp) | 10-15 | db.t3.micro | $15 |
| Medium (500 emp) | 20-30 | db.t3.small | $25 |
| Large (2,500 emp) | 40-60 | db.t3.medium | $60 |
| Enterprise (10K emp) | 80-100 | db.t3.large | $130 |

**Connection cost model:**
- Pool: 5 base + 5 overflow per worker
- 4 workers per ECS task = 40 connections per task
- Production typically runs 2-4 tasks
- Total: 80-160 concurrent connections

#### Database Storage
| Customer Size | Schedules/mo | Employees | Est. Storage | Monthly Cost |
|---|---|---|---|---|
| Small | 4 | 50 | 0.5 GB | $1 |
| Medium | 20 | 500 | 2 GB | $2 |
| Large | 80 | 2,500 | 8 GB | $4 |
| Enterprise | 400 | 10,000 | 35 GB | $10 |

**Storage growth rate:**
- ~50 MB per 1,000 employees (profiles, availability, affinities)
- ~5 MB per 100 schedules (shift records, changes)
- ~2 GB per year for logs/audit

#### Backup & Standby Costs
- Automated backups: ~20% of primary storage cost
- Multi-AZ standby (if enabled): 100% of primary instance cost

---

### 2. **ECS Fargate Compute Costs**

#### Web API (Always Running)
| vCPU | Memory | Tasks | On-Demand/mo | Spot/mo |
|------|--------|-------|--------------|---------|
| 0.25 | 512 MB | 1-2 | $7 | $2 |
| 0.5 | 1 GB | 2-4 | $14 | $4 |
| 1.0 | 2 GB | 2-4 | $28 | $8 |
| 2.0 | 4 GB | 2-4 | $56 | $16 |

**Production typical**: 0.5 vCPU × 4 tasks = **$56/month on-demand**

#### Scheduling Job (Triggered per Generation)
- **Frequency**: 20-400 runs/month (customer dependent)
- **Duration**: 30-60 seconds per run
- **Compute**: 1 vCPU, 2 GB RAM (Claude context processing)
- **Cost per run**: ~$0.002-0.003

| Customer Type | Runs/mo | Est. Compute Cost |
|---|---|---|
| Small | 4 | $0.01 |
| Medium | 20 | $0.05 |
| Large | 80 | $0.20 |
| Enterprise | 400 | $1.00 |

**Note**: Scheduling job is **the highest variable cost** for heavy users.

---

### 3. **Network Costs**

#### Data Transfer
- **Inbound**: $0 (AWS to user)
- **Outbound**: $0.09 per GB (user to internet)
- **RDS egress**: Included within same region
- **CloudFront to user**: $0.085 per GB (if using CDN for assets)

| Customer Type | Est. Monthly Data Out | Network Cost |
|---|---|---|
| Small API user | 100 MB | $0.01 |
| Heavy API user | 500 MB | $0.05 |
| Large export user | 2 GB | $0.18 |

**Typical**: Most users < $0.05/month

---

### 4. **Supporting Services**

| Service | Cost | Notes |
|---------|------|-------|
| RDS Enhanced Monitoring | $11-15 | CloudWatch metrics + logs |
| CloudWatch Logs | $0.50-2 | Schedule generation logs |
| Secrets Manager | $0.40 | Database credentials |
| NAT Gateway | $0.045/hour | If needed for egress (~$33/month) |
| Load Balancer | $16.20/month | NLB for Fargate |
| **Total Supporting** | ~$25-60/month | Varies with scale |

---

## Monthly Infrastructure Cost by Customer Profile

### Profile A: Small Café (50 employees, 4 schedules/month)
```
RDS Instance (micro)           $15.00
RDS Storage (0.5 GB)           $1.00
RDS Backup (~20%)              $3.00
ECS Fargate Web (shared)        $8.00  (1/4 of shared 4-task setup)
Scheduling job (4 runs)        $0.01
Network/egress                 $0.01
Supporting services (shared)    $6.00  (1/4 of baseline)
─────────────────────────────────────
Total Monthly Cost             $33.03
```
**vs Revenue $18.21** → **Loss: -$14.82**

### Profile B: Restaurant Group (500 employees, 20 schedules/month)
```
RDS Instance (small)           $25.00
RDS Storage (2 GB)             $2.00
RDS Backup (~20%)              $5.40
ECS Fargate Web                $14.00  (1/4 of 0.5 vCPU setup)
Scheduling job (20 runs)       $0.06
Network/egress                 $0.03
Supporting services            $9.00
─────────────────────────────────────
Total Monthly Cost             $55.49
```
**vs Revenue $19.04** → **Loss: -$36.45**

### Profile C: Large Chain (2,500 employees, 80 schedules/month)
```
RDS Instance (medium)          $60.00
RDS Storage (8 GB)             $4.00
RDS Backup (~20%)              $12.80
ECS Fargate Web                $28.00  (1/2 of 1.0 vCPU setup)
Scheduling job (80 runs)       $0.24
Network/egress                 $0.15
Supporting services            $15.00
─────────────────────────────────────
Total Monthly Cost             $120.19
```
**vs Revenue $22.42** → **Loss: -$97.77**

### Profile D: Regional Chain (10,000 employees, 400 schedules/month)
```
RDS Instance (large)           $130.00
RDS Storage (35 GB)            $10.00
RDS Backup (~20%)              $28.00
RDS Multi-AZ Standby (optional) $130.00
ECS Fargate Web                $56.00  (4 tasks × 0.5 vCPU)
Scheduling job (400 runs)      $1.20
Network/egress                 $0.40
Supporting services            $30.00
─────────────────────────────────────
Total Monthly Cost             $385.60 (w/o Multi-AZ: $255.60)
```
**vs Revenue $48.50** → **Loss: -$337.10** (or -$207 w/o Multi-AZ)

---

## The Real Problem: Infrastructure Doesn't Scale Linearly with Pricing

### Cost Scaling vs Revenue Scaling

| Customer Size | Monthly Revenue | Infrastructure Cost | Margin | Issue |
|---|---|---|---|---|
| **Small (50 emp)** | $18.21 | $33 | -82% | 🔴 **LOSING MONEY** |
| **Medium (500 emp)** | $19.04 | $55 | -191% | 🔴 **LOSING MONEY** |
| **Large (2.5K emp)** | $22.42 | $120 | -436% | 🔴 **LOSING MONEY** |
| **Enterprise (10K emp)** | $48.50 | $256 | -429% | 🔴 **LOSING MONEY** |

**Every single customer profile is underwater.**

---

## Root Cause Analysis

### Why Infrastructure Costs Are So High

1. **Fixed Baseline**: 
   - RDS instance minimum = $15/month (even for 1 user)
   - ECS web tasks = $14-56/month (even if idle)
   - Supporting services = $25-60/month
   - **Fixed cost floor: ~$55-120/month regardless of customer size**

2. **Scheduling Job is the Variable Killer**:
   - Each schedule generation spins up a new Fargate task
   - 30-60 seconds of execution time
   - Cost: **$0.002-0.003 per run**
   - Enterprise (400 runs/mo): $1.20 in compute alone
   - This scales directly with customer usage

3. **RDS Doesn't Share Efficiently**:
   - Can't easily multi-tenant across companies (data isolation concerns)
   - Need separate instances for different security/compliance levels
   - Connection pooling has hard limits

4. **Network Egress Is Cheap But Not Free**:
   - Large exports (payroll data, reports) hit egress charges
   - $0.09/GB adds up

---

## What Needs to Change: Pricing Recommendations

### Option 1: Increase Base Price Significantly
```
Current: $18/month base
Proposed: $50-75/month base + variable overages

Rationale:
- Covers fixed infrastructure ($55-120/month)
- Allows profit margin on variable costs
- Makes economic sense at all scales
```

**Pros**: 
- Sustainable business model
- Works for all customer sizes

**Cons**: 
- Dramatically reduces competitiveness
- 7shifts charges $3-7/user, you'd be ~$50-75 flat
- Acquisition friction for small teams

### Option 2: Per-Manager or Per-User Pricing (Hybrid)
```
Base: $25-35/month
+ $5-10 per manager/user (not per employee)

Example: 
- 50-person café with 2 managers: $35 + ($10 × 2) = $55/month
- 2,500-person chain with 20 managers: $35 + ($10 × 20) = $235/month

Rationale:
- Managers create schedules (drive job execution)
- Scales with actual infrastructure usage
- More granular than employee count
```

**Pros**: 
- Closer to actual cost drivers
- Fairer for small teams
- Competitive with per-user pricing

**Cons**: 
- More complex pricing model
- Still may not fully cover costs for large teams

### Option 3: Scheduling-Based Pricing (Usage-Driven)
```
Base: $30/month
+ $0.05-0.10 per schedule generation

Example:
- Small (4 schedules): $30 + ($0.10 × 4) = $30.40/month
- Medium (20 schedules): $30 + ($0.10 × 20) = $32/month
- Enterprise (400 schedules): $30 + ($0.10 × 400) = $70/month

Rationale:
- Directly tied to compute cost
- Transparently shows scaling
- Fair for heavy users
```

**Pros**: 
- Directly matches cost driver (Fargate jobs)
- Easy to understand
- Flexible pricing

**Cons**: 
- Could discourage adoption ("pay per use anxiety")
- May hurt "unlimited schedules" positioning

### Option 4: Adopt Committed Use Discounts (AWS)
```
Current: Pay-as-you-go RDS/Fargate
Proposed: 1-year committed capacity

Saves: 30-40% on RDS + Fargate costs

But requires customer lock-in
```

---

## Recommendation

**Implement hybrid approach:**

1. **Immediate**: Increase base from $18 → **$35-40/month**
   - Covers fixed infrastructure
   - Still 4-7x cheaper than per-user competitors
   - Small teams can justify cost (one manager's hourly wage)

2. **Longer term**: Add per-schedule-generation charge (~$0.05-0.10)
   - Aligns incentives
   - Covers variable compute costs
   - Makes enterprise tiers profitable

3. **Optional**: Add manager count multiplier (+$2-5 per manager)
   - Alternative to per-schedule charging
   - Better for cost accounting visibility

---

## Current State Summary

**The hard truth:**
- Your current pricing is **unsustainable**
- Every customer profile loses money on infrastructure alone
- This only works if you're running at extreme scale or have investor subsidy
- Need to increase base price by 50-100% minimum

**Examples with new $35 base:**
| Profile | Old Revenue | New Revenue | Est. Cost | Margin |
|---|---|---|---|---|
| Small | $18 | $35 | $33 | +6% |
| Medium | $19 | $36 | $55 | -35% |
| Large | $22 | $40 | $120 | -67% |
| Enterprise | $48 | $65 | $256 | -75% |

**Even at $35 base, large customers are still unprofitable.** Need either:
- Higher base ($75-100)
- Per-schedule/manager charges
- Or accept losses on heavy users (venture model)


---

## Scaling Model: Locations vs Employees vs Cost

### Database Storage Scaling

**Per employee**: ~50 KB (profiles, availability, preferences, affinities)
**Per location**: ~100 KB (config, templates, rules)
**Per schedule**: ~5 KB (shift records)
**Logs/audit**: ~2 GB/year

### Example: How Costs Scale

#### Scenario 1: Single Location, Growing Employees
```
Location: 1
Growth path: 100 → 500 → 1,000 → 2,500 employees

Employees | Storage | RDS Instance | Monthly RDS Cost
100       | 0.6 GB  | t3.micro     | $15
500       | 1.2 GB  | t3.small     | $25
1,000     | 2.0 GB  | t3.medium    | $60
2,500     | 3.8 GB  | t3.large     | $130
```

#### Scenario 2: Growing Locations, Fixed Employees per Location
```
Employees per location: 100
Growth path: 1 → 5 → 10 → 20 locations

Locations | Total Employees | Concurrent Jobs | Storage | RDS Instance | Monthly Cost
1         | 100             | 1/month         | 0.6 GB  | t3.micro     | $15
5         | 500             | 5/month         | 1.2 GB  | t3.small     | $25
10        | 1,000           | 10/month        | 2.0 GB  | t3.medium    | $60
20        | 2,000           | 20/month        | 3.8 GB  | t3.large     | $130
```

**Note:** Locations affect scheduling job frequency more than storage.
- 1 location = 1 concurrent job per generation
- 10 locations = 10 concurrent jobs (may need horizontal scaling)
- 20+ locations = Need job queue + multiple workers

#### Scenario 3: Cross-Functional Scaling (Locations × Employees)
```
Locations: 5
Employees per location: 200
Total employees: 1,000

Metric                    | Value           | Cost
─────────────────────────────────────────────────────
Total employees           | 1,000           | RDS +$0
Total locations           | 5               | Job queue scaling
Storage                   | 2.0 GB          | RDS +$2
Concurrent connections    | ~40-60          | t3.medium = $60
Scheduling jobs/month     | ~100 (5×20)     | Fargate $0.30
Monthly infrastructure    |                 | ~$85-100

Revenue at $35 base       | $35             | ❌ NEGATIVE MARGIN
```

---

## Cost Scaling Rules of Thumb

### RDS Instance Sizing
```
Employees    | Locations | Est. Instance | Monthly Cost
0-100        | 1-3       | t3.micro      | $15
100-500      | 3-10      | t3.small      | $25
500-2,000    | 5-20      | t3.medium     | $60
2,000-5,000  | 10-50     | t3.large      | $130
5,000-10,000 | 20-100    | t3.xlarge     | $230
10,000+      | 100+      | Custom        | $500+
```

### Scheduling Job Costs
```
Locations per month | Runs/month | Est. Fargate Cost
1                   | 4          | $0.01
5                   | 20         | $0.06
10                  | 100        | $0.30
20                  | 400        | $1.20
50                  | 1,000      | $3.00
100                 | 4,000      | $12.00
```

**Key insight:** Scheduling job cost grows linearly with locations × schedules/month.

---

## Pricing Implications: Locations Should Drive Cost

**Current pricing ignores locations entirely:**
- 1 location @ $18/month = same price as 50 locations @ $18/month
- But 50 locations runs 250+ scheduling jobs vs 4 jobs
- That's 60x more compute cost for the same revenue

**This is why large enterprise is unprofitable.**

### Revised Recommendation: Location-Based Pricing

```
Base: $35/month for 1 location
+ $15-20 per additional location

Examples:
- 1 location (100 emp):         $35/month
- 5 locations (500 emp):        $35 + (4×$15) = $95/month
- 10 locations (1,000 emp):     $35 + (9×$15) = $170/month
- 50 locations (5,000 emp):     $35 + (49×$15) = $770/month
- 100 locations (10,000 emp):   $35 + (99×$15) = $1,520/month

Rationale:
- Each location = separate scheduling job execution
- Job cost scales directly: ~$0.003 per job
- $15-20/location covers that + some RDS scaling
- Makes economic sense for all scales
```

**Comparison to revenue:**
```
Locations | Schedules/mo | Revenue | Cost | Margin
1         | 4            | $35     | $33  | +6%
5         | 20           | $50     | $55  | -9%
10        | 100          | $65     | $85  | -31%
50        | 500          | $200    | $370 | -85%
100       | 1,000        | $385    | $770 | -100%
```

**Still underwater at scale.** Need to also increase:
- Base price to $50-75
- Per-location to $25-30
- OR add per-schedule charge ($0.05-0.10)

---

## Final Verdict

**Sustainable pricing needs to reflect three costs:**

1. **Base infrastructure**: $35-50/month
   - Fixed RDS, ECS, monitoring costs

2. **Per-location**: $15-25/month
   - Scheduling job execution (~$0.003 per job × ~20 jobs typical)
   - Additional RDS connection overhead

3. **Per-schedule overage** (optional): $0.05-0.10
   - Direct Fargate compute cost passthrough
   - Transparent to customer

**Example sustainable model:**
```
Base:                    $40
+ Location 1:            $15
+ Locations 2-5:         $12 each
+ Locations 6+:          $10 each
+ Overages:              Same as current ($1/1K emp, $0.10/50 schedules)

5-location company:  $40 + $15 + (4×$12) = $103/month
vs Current:         ~$22/month

100-location company: $40 + $15 + (4×$12) + (90×$10) = $973/month
vs Current:         ~$48/month
```

**This is 4-20x current pricing, but:**
- Actual infrastructure cost justifies it
- Still competitive with per-user pricing (7shifts = $3-7 × 20-100 managers)
- Makes every customer profitable

