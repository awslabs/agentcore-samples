---
name: revenue-growth-analyst
description: Deep-dives into revenue growth patterns, growth rates, and growth quality.
  Use when the user asks specifically about revenue growth, top-line performance,
  sales growth, revenue acceleration or deceleration, growth trajectory, or wants
  to understand what is driving revenue changes. NOT for cost or margin analysis —
  this skill is revenue-focused only.
metadata:
  version: "1.0"
  tags: finance, revenue, growth, top-line
mcp_tools:
  - get_financial_data
  - get_kpi_benchmarks
---

# Revenue Growth Analyst

Provides an in-depth analysis of revenue growth patterns across quarters,
including absolute growth, growth rate trends, and growth quality assessment.

## Prerequisites

No inputs required unless the user specifies a particular quarter to focus on.
Default: analyze all available quarters (Q4 2024 through Q3 2025).

## Steps

### Step 1: Fetch all quarterly revenue data

    get_financial_data(period="Q4 2024")
    get_financial_data(period="Q1 2025")
    get_financial_data(period="Q2 2025")
    get_financial_data(period="Q3 2025")

Extract the `revenue` field from each response.

### Step 2: Fetch growth benchmarks

    get_kpi_benchmarks()

Extract `revenue_growth_qoq_pct`: high_growth_benchmark (20%) and
stable_growth_benchmark (5%).

### Step 3: Calculate growth metrics

Use python_exec to compute:

```python
revenues = {
    "Q4 2024": 4000000,
    "Q1 2025": 3500000,
    "Q2 2025": 3800000,
    "Q3 2025": 4200000,
}
order = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025"]

results = {}
for i in range(1, len(order)):
    curr_q = order[i]
    prev_q = order[i-1]
    curr_r = revenues[curr_q]
    prev_r = revenues[prev_q]
    abs_growth = curr_r - prev_r
    pct_growth = round(abs_growth / prev_r * 100, 1)
    results[curr_q] = {
        "absolute_growth": abs_growth,
        "pct_growth": pct_growth,
    }
    label = "HIGH" if pct_growth >= 20 else ("STABLE" if pct_growth >= 5 else "LOW/NEGATIVE")
    print(f"{prev_q} → {curr_q}: ${abs_growth:+,} ({pct_growth:+.1f}%)  [{label}]")

# Cumulative growth from Q4 2024 baseline
baseline = revenues["Q4 2024"]
latest   = revenues["Q3 2025"]
cumulative = round((latest - baseline) / baseline * 100, 1)
print(f"\nCumulative growth Q4 2024 → Q3 2025: {cumulative:+.1f}%")

# Peak and trough
peak  = max(revenues, key=revenues.get)
trough = min(revenues, key=revenues.get)
print(f"Peak quarter: {peak} (${revenues[peak]:,})")
print(f"Trough quarter: {trough} (${revenues[trough]:,})")
```

### Step 4: Assess growth quality

Evaluate:
- **Consistency**: Is growth maintained quarter over quarter, or volatile?
- **Momentum**: Is the growth rate itself accelerating or decelerating?
- **Benchmark vs actuals**: How do QoQ growth rates compare to stable (5%) and high (20%) benchmarks?

### Step 5: Present results

Format your response as:

1. **Revenue Growth Table**

   | Period | Revenue | QoQ Change | QoQ % | Growth Category |
   |--------|---------|-----------|-------|-----------------|

2. **Growth Momentum Assessment** — 1–2 sentences on whether momentum is
   building, stalling, or recovering.

3. **Cumulative Performance** — single number: total revenue growth from the
   oldest to the most recent quarter.

4. **CEO-level Takeaway** — one sentence summarizing the revenue story.
