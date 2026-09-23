---
name: multi-quarter-trend-analysis
description: Analyzes financial trends across multiple quarters by comparing P&L metrics
  over time. Use when the user wants to see trends, patterns, trajectories, or
  directional movement across 3 or more quarters. Also use for "how are we trending",
  "show me the trend", "track performance over time", "quarter over quarter comparison
  across all quarters", or any multi-period longitudinal analysis.
metadata:
  version: "1.0"
  tags: finance, trend, multi-quarter, analysis
mcp_tools:
  - get_financial_data
  - get_kpi_benchmarks
---

# Multi-Quarter Trend Analysis

Analyzes P&L data across all available quarters to identify directional trends
and flag acceleration or deceleration in key financial metrics.

## Prerequisites

No inputs required from the user — all data is fetched from the MCP server.
Available quarters: Q1 2025, Q2 2025, Q3 2025, Q4 2024.

## Steps

### Step 1: Fetch all quarterly P&L data

Call get_financial_data for each quarter:

    get_financial_data(period="Q4 2024")
    get_financial_data(period="Q1 2025")
    get_financial_data(period="Q2 2025")
    get_financial_data(period="Q3 2025")

Store all four results. You now have a time series of: revenue, cogs,
operating_expenses, ebitda.

### Step 2: Fetch benchmark thresholds

    get_kpi_benchmarks()

Store the formulas and benchmarks for use in Step 4.

### Step 3: Calculate derived metrics for each quarter

Use python_exec to compute the following for every quarter:

- Gross Margin %          = (Revenue - COGS) / Revenue * 100
- EBITDA Margin %         = EBITDA / Revenue * 100
- Operating Expense Ratio = Operating Expenses / Revenue * 100
- QoQ Revenue Growth %    = (Current Revenue - Prior Revenue) / Prior Revenue * 100
  (Q4 2024 has no prior quarter — mark as N/A)

Example:
```python
quarters = {
    "Q4 2024": {"revenue": 4000000, "cogs": 1800000, "opex": 1000000, "ebitda": 1200000},
    "Q1 2025": {"revenue": 3500000, "cogs": 1575000, "opex": 910000,  "ebitda": 1015000},
    "Q2 2025": {"revenue": 3800000, "cogs": 1710000, "opex": 980000,  "ebitda": 1110000},
    "Q3 2025": {"revenue": 4200000, "cogs": 1890000, "opex": 1050000, "ebitda": 1260000},
}

order = ["Q4 2024", "Q1 2025", "Q2 2025", "Q3 2025"]
results = {}
for i, q in enumerate(order):
    d = quarters[q]
    gm     = round((d["revenue"] - d["cogs"]) / d["revenue"] * 100, 1)
    em     = round(d["ebitda"] / d["revenue"] * 100, 1)
    opex_r = round(d["opex"] / d["revenue"] * 100, 1)
    if i > 0:
        prev_rev = quarters[order[i-1]]["revenue"]
        qoq = round((d["revenue"] - prev_rev) / prev_rev * 100, 1)
    else:
        qoq = None
    results[q] = {"gross_margin": gm, "ebitda_margin": em, "opex_ratio": opex_r, "qoq_growth": qoq}
    print(f"{q}: GM={gm}%  EBITDA={em}%  OpEx={opex_r}%  QoQ={qoq}%")
```

### Step 4: Identify trends

For each metric, determine the directional trend across the four quarters:
- **Improving**: consistently moving in the favorable direction
- **Declining**: consistently moving in the unfavorable direction
- **Mixed**: alternating or no clear direction

Flag any quarter where a metric crosses a benchmark threshold boundary
(e.g. drops from GREEN to YELLOW or YELLOW to RED).

### Step 5: Present results

Format your response as:

1. **Trend Summary Table** — one row per metric, columns = quarters + trend arrow (↑ ↓ →)

   | Metric | Q4 2024 | Q1 2025 | Q2 2025 | Q3 2025 | Trend |
   |--------|---------|---------|---------|---------|-------|

2. **Revenue Trajectory** — highlight the dip in Q1 2025 and recovery narrative.

3. **3 Key Observations** — the most important findings for leadership, each in one sentence.
