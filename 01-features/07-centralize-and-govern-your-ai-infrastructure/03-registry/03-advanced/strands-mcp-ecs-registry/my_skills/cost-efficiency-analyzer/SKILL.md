---
name: cost-efficiency-analyzer
description: Analyzes cost structure, cost efficiency, and expense management from P&L data.
  Use when the user asks about costs, expenses, COGS, operating expenses, cost ratios,
  cost control, spending efficiency, margin compression from cost side, or wants to
  understand where money is going. Also use for "are we spending too much", "cost breakdown",
  "expense analysis", or "how efficient are our operations". NOT for revenue or top-line analysis.
metadata:
  version: "1.0"
  tags: finance, cost, expenses, efficiency, operations
mcp_tools:
  - get_financial_data
  - get_kpi_benchmarks
---

# Cost Efficiency Analyzer

Analyzes cost structure and operational efficiency by examining COGS,
operating expenses, and their ratios relative to revenue over time.

## Prerequisites

No inputs required unless the user asks about a specific quarter.
Default: analyze the most recent quarter (Q3 2025) with comparison to Q2 2025.

## Steps

### Step 1: Fetch cost data

Fetch the quarter(s) needed:

    get_financial_data(period="Q3 2025")
    get_financial_data(period="Q2 2025")

If the user asks for a different quarter, fetch that instead.

### Step 2: Fetch benchmarks

    get_kpi_benchmarks()

Extract:
- `gross_margin_pct`: formula and benchmark (40%)
- `opex_ratio`: formula and benchmark (30%) — note: lower is better

### Step 3: Compute cost metrics

Use python_exec to calculate cost efficiency metrics:

```python
# Q3 2025 data
r3  = 4200000; cogs3 = 1890000; opex3 = 1050000; ebitda3 = 1260000

# Q2 2025 data (for comparison)
r2  = 3800000; cogs2 = 1710000; opex2 = 980000;  ebitda2 = 1110000

def cost_metrics(revenue, cogs, opex, ebitda, label):
    gross_profit  = revenue - cogs
    gross_margin  = round(gross_profit / revenue * 100, 1)
    cogs_pct      = round(cogs / revenue * 100, 1)
    opex_pct      = round(opex / revenue * 100, 1)
    total_cost    = cogs + opex
    total_cost_pct = round(total_cost / revenue * 100, 1)
    ebitda_margin = round(ebitda / revenue * 100, 1)
    cost_per_rev  = round(total_cost / revenue, 4)   # $ of cost per $ of revenue

    print(f"\n{label}:")
    print(f"  COGS:                ${cogs:,}  ({cogs_pct}% of revenue)")
    print(f"  Operating Expenses:  ${opex:,}  ({opex_pct}% of revenue)")
    print(f"  Total Cost:          ${total_cost:,}  ({total_cost_pct}% of revenue)")
    print(f"  Gross Margin:        {gross_margin}%  (benchmark: 40%)")
    print(f"  EBITDA Margin:       {ebitda_margin}%  (benchmark: 15%)")
    print(f"  Cost per $1 revenue: ${cost_per_rev:.4f}")
    return {"gross_margin": gross_margin, "opex_pct": opex_pct, "cogs_pct": cogs_pct,
            "total_cost_pct": total_cost_pct}

m3 = cost_metrics(r3, cogs3, opex3, ebitda3, "Q3 2025")
m2 = cost_metrics(r2, cogs2, opex2, ebitda2, "Q2 2025")

# QoQ cost efficiency change
print(f"\nQoQ Cost Efficiency Change (Q2 → Q3):")
print(f"  COGS ratio:  {m2['cogs_pct']}% → {m3['cogs_pct']}%  ({m3['cogs_pct']-m2['cogs_pct']:+.1f}pp)")
print(f"  OpEx ratio:  {m2['opex_pct']}% → {m3['opex_pct']}%  ({m3['opex_pct']-m2['opex_pct']:+.1f}pp)")
print(f"  Total cost%: {m2['total_cost_pct']}% → {m3['total_cost_pct']}%  ({m3['total_cost_pct']-m2['total_cost_pct']:+.1f}pp)")
```

### Step 4: Benchmark assessment

For each cost ratio, assign status vs benchmark:
- Gross Margin:  GREEN ≥ 40%, YELLOW 35–40%, RED < 35%
- OpEx Ratio:    GREEN ≤ 30%, YELLOW 30–35%, RED > 35%  (lower is better)

### Step 5: Present results

Format your response as:

1. **Cost Structure Table** — current quarter

   | Cost Item | Amount | % of Revenue | Benchmark | Status |
   |-----------|--------|-------------|-----------|--------|

2. **QoQ Cost Efficiency** — did cost ratios improve or worsen vs prior quarter?

3. **Cost Efficiency Verdict** — one paragraph: is cost management healthy,
   where is the risk, and what should leadership watch?
