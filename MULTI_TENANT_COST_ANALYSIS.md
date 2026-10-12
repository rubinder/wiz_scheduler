# WizScheduler Cost Analysis: Multi-Tenant (Shared RDS)

## Corrected Model: All Customers Share One RDS Instance

### Fixed Infrastructure (Shared Across All Customers)

| Cost Component | Monthly Cost | Notes |
|---|---|---|
| **RDS Instance** | $60 | t3.medium, shared by ALL customers |
| **RDS Storage** | $4 | 8 GB total for ALL customers |
| **RDS Backup** | $10 | ~20% of storage |
| **ECS Fargate (Web)** | $28 | 1.0 vCPU, handles all API traffic |
| **Supporting services** | $30 | CloudWatch, monitoring, NAT, ALB |
| **────────────────** | | |
| **TOTAL FIXED** | **$132/month** | Shared across all customers |

### Variable Cost Per Customer

| Cost Component | Per Location | Notes |
|---|---|---|
| **Scheduling job** | $0.002-0.003 | Fargate task execution |
| **Storage** | ~$0.001/month | ~10-50 MB per customer |
| **Network egress** | $0.001 | Minimal (API is small) |
| **────────────────** | | |
| **TOTAL VAR/LOCATION** | **~$0.003** | Plus per-employee/schedule overages |

---

## Economics with Shared Infrastructure

### How to Calculate Customer Profitability

**Fixed cost per customer** = $132 / number of customers

**Total cost per customer** = (Fixed cost share) + (Variable per location)

---

## Examples: 10 Active Customers

### Fixed Cost Share (per customer)
$132 / 10 = **$13.20/month each**

### Customer 1: Single Location, 50 Employees
```
Fixed share:                    $13.20
Variable (1 location @ $0.003): $0.01
Storage (~50 MB):               $0.01
─────────────────────────────────
Total Cost:                     $13.22
Revenue (current):             $18.00
Profit:                        +$4.78 ✅
```

### Customer 2: 5 Locations, 500 Employees
```
Fixed share:                    $13.20
Variable (5 locations):         $0.02
Storage (~300 MB):              $0.15
─────────────────────────────────
Total Cost:                     $13.37
Revenue (current):             $22-25
Profit:                        +$8-12 ✅
```

### Customer 3: 10 Locations, 2,500 Employees
```
Fixed share:                    $13.20
Variable (10 locations):        $0.04
Storage (~2 GB):               $1.00
─────────────────────────────────
Total Cost:                     $14.24
Revenue (current):             $25-30
Profit:                        +$11-16 ✅
```

### Customer 4: 50 Locations, 10,000 Employees
```
Fixed share:                    $13.20
Variable (50 locations):        $0.20
Storage (~8 GB):               $4.00
─────────────────────────────────
Total Cost:                     $17.40
Revenue (current):             $48-50
Profit:                        +$31-33 ✅
```

---

## Scaling Scenarios

### Scenario A: 10 Customers (Current State, Estimate)
```
Fixed costs:                    $132/month
Average customer:               ~5 locations, 500 employees
Avg revenue per customer:       ~$22/month
Avg variable cost:              ~$1/month
────────────────────────────────────────
Avg total cost/customer:        $13.20 + $1 = $14.20
Avg profit/customer:           +$7.80 ✅
Total profit:                  +$78/month
```

### Scenario B: 50 Customers
```
Fixed costs:                    $132/month
Fixed per customer:             $132/50 = $2.64
Avg revenue per customer:       ~$22/month (assuming similar mix)
Avg variable cost:              ~$1/month
────────────────────────────────────────
Avg total cost/customer:        $2.64 + $1 = $3.64
Avg profit/customer:           +$18.36 ✅
Total profit:                  +$918/month
```

### Scenario C: 100 Customers
```
Fixed costs:                    $132/month
Fixed per customer:             $132/100 = $1.32
Avg revenue per customer:       ~$22/month
Avg variable cost:              ~$1/month
────────────────────────────────────────
Avg total cost/customer:        $1.32 + $1 = $2.32
Avg profit/customer:           +$19.68 ✅
Total profit:                  +$1,968/month
```

---

## Key Insights with Shared RDS

### 1. **You're Actually Profitable Right Now**
- With shared infrastructure, most customer profiles are profitable
- Even a 1-location customer costs ~$13-14 to serve, and pays $18
- Margins improve dramatically as you add customers

### 2. **Scale Economics Work in Your Favor**
- As customer count grows, fixed cost per customer shrinks
- 10 customers: $13.20 fixed/customer → 73% of their cost
- 100 customers: $1.32 fixed/customer → 6% of their cost
- Profitability increases with scale

### 3. **Variable Cost is Minimal**
- Only $0.003 per location in scheduling job compute
- Storage growth is negligible per customer (~10-50 MB)
- Real variable cost driver: **scale of customer base, not size of individual customers**

### 4. **Locations Are Still the Cost Driver**
- But impact is much smaller (~$0.003/location)
- Small restaurants (1-5 locations) cost almost nothing extra
- Even 100-location enterprise only adds ~$0.30/month in variable

---

## Pricing Recommendation: With Shared RDS

### Current Pricing Works Well

**Keep base $18/month, but you could:**

1. **Option A: Keep as-is**
   - Sustainable with 10+ customers
   - Healthy margins at scale
   - Good for customer acquisition

2. **Option B: Small increase to $25/month base**
   - More buffer for customer acquisition costs
   - Still very competitive
   - Improve unit economics early

3. **Option C: Add location-based charge (optional)**
   - $2-5 per additional location
   - Charges for real compute cost (though minimal)
   - Better reflects usage

---

## Break-Even Analysis

### How many customers do you need?

**Assumption: Average customer pays $20/month, has 2-3 locations**

| Customer Count | Monthly Fixed Cost | Revenue | Profit/Loss |
|---|---|---|---|
| 5 | $132 | $100 | **-$32** 🔴 |
| 7 | $132 | $140 | **+$8** 🟡 |
| 10 | $132 | $200 | **+$68** 🟢 |
| 15 | $132 | $300 | **+$168** 🟢 |
| 20 | $132 | $400 | **+$268** 🟢 |

**Break-even: ~7 active paying customers**

Currently (if you have 10+): You're profitable.

---

## Revised Bottom Line

### With Shared RDS Infrastructure:

✅ **Current pricing ($18 base) is sustainable**
- Fixed cost shared across customers makes margins work
- Even small customers are profitable
- Scale improves economics

✅ **You don't need to increase base price**
- But could increase to $25 for more runway

✅ **Don't charge per-location**
- Variable cost is negligible ($0.003)
- Adds complexity without financial justification

✅ **Focus on customer acquisition**
- Each new customer adds ~$20 revenue
- Only adds ~$1-2 in variable cost
- Every customer improves unit economics

---

## The Real Business Metric

**Not "margin per customer," but "contribution per customer":**

| Metric | Current Model |
|---|---|
| Avg revenue/customer | $20-25 |
| Avg variable cost/customer | $1-2 |
| **Contribution margin** | **$18-24** |
| Fixed monthly cost | $132 |
| Contribution/customer | 90% |

**This is healthy.** You need just 5-6 customers to breakeven on fixed costs, then profit compounds from there.

