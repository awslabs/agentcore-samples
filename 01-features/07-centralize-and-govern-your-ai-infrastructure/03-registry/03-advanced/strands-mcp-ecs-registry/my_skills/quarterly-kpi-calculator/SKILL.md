---
name: quarterly-kpi-calculator
description: Calculates quarterly financial KPIs from P&L data. P&L figures can be
  provided directly by the user or fetched from the financial data MCP server.
  Use when the user wants KPI calculations such as Gross Margin %, EBITDA Margin %,
  Operating Expense Ratio, or Revenue Growth % QoQ. Also use for quarterly
  performance review, P&L analysis, or interpreting financial ratios against benchmarks.
metadata:
  version: "2.0"
  tags: finance, kpi, analysis, mcp
mcp_tools:
  - get_financial_data
  - get_kpi_benchmarks
---

# Quarterly KPI Calculator

Calculates and interprets financial KPIs. P&L data is fetched from the financial
MCP server or taken from figures the user provides.

## Prerequisites

At minimum: Revenue and COGS (provided by user or fetched via get_financial_data).
Optional: EBITDA, Operating Expenses, prior quarter Revenue for QoQ growth.

## Steps

### Step 1: Retrieve benchmark thresholds

Call the get_kpi_benchmarks tool to get current KPI formulas and benchmark values:

    get_kpi_benchmarks()

Store the result — you will use the formulas and benchmarks in Steps 3 and 4.

### Step 2: Get P&L data

**If the user provided P&L figures directly** (Revenue, COGS, EBITDA, Operating Expenses),
use those values.

**If the user specified only a quarter** (e.g. "Q3 2025") without raw figures,
call get_financial_data to retrieve them:

    get_financial_data(period="Q3 2025")

**If QoQ Revenue Growth is requested** and prior quarter data is needed,
call get_financial_data for the prior quarter as well:

    get_financial_data(period="Q2 2025")

### Step 3: Calculate KPIs

Use python_exec to calculate the following from the P&L data (use values from Step 2):

- Gross Margin %           = (Revenue - COGS) / Revenue * 100
- EBITDA Margin %          = EBITDA / Revenue * 100              (if EBITDA available)
- Operating Expense Ratio  = Operating Expenses / Revenue * 100  (if OpEx available)
- Revenue Growth % QoQ     = (Current - Prior) / Prior * 100     (if prior available)

Round all percentages to one decimal place.

Example:
```python
revenue   = 4200000
cogs      = 1890000
ebitda    = 1260000
opex      = 1050000
prior_rev = 3800000

gross_margin  = round((revenue - cogs) / revenue * 100, 1)
ebitda_margin = round(ebitda / revenue * 100, 1)
opex_ratio    = round(opex / revenue * 100, 1)
rev_growth    = round((revenue - prior_rev) / prior_rev * 100, 1)

print(f"Gross Margin:            {gross_margin}%")
print(f"EBITDA Margin:           {ebitda_margin}%")
print(f"Operating Expense Ratio: {opex_ratio}%")
print(f"Revenue Growth QoQ:      {rev_growth}%")
```

### Step 4: Interpret against benchmarks

Using general_benchmark values from get_kpi_benchmarks (Step 1), assign each KPI a status:
- GREEN  : at or above general_benchmark
- YELLOW : within 5 percentage points below general_benchmark
- RED    : more than 5 percentage points below general_benchmark

### Step 5: Present results

Format your final response as:
1. A KPI results table: | Metric | Value | Benchmark | Status |
2. A 2–3 sentence executive commentary on the most significant finding.
