# Financial KPI Formulas and Benchmarks

## Core Formulas

### Gross Margin %
Formula : (Revenue - COGS) / Revenue * 100
SaaS benchmark          : >= 70%
General business benchmark : >= 40%
Interpretation: Measures how much revenue remains after direct costs.
Higher is better. A declining trend warrants investigation into COGS drivers.

### EBITDA Margin %
Formula : EBITDA / Revenue * 100
SaaS benchmark          : >= 20%
General business benchmark : >= 15%
Interpretation: Proxy for operating cash flow efficiency before capital structure
and tax effects. Used for cross-company comparisons.

### Operating Expense Ratio
Formula : Operating Expenses / Revenue * 100
SaaS benchmark          : <= 40%
General business benchmark : <= 30%
Interpretation: Lower is better. A high ratio relative to gross margin compresses
net income. Watch for trend increases over consecutive quarters.

### Revenue Growth % (Quarter-over-Quarter)
Formula : (Current Revenue - Prior Revenue) / Prior Revenue * 100
High-growth benchmark   : >= 20% QoQ
Stable-growth benchmark : >= 5%  QoQ
Interpretation: Measures top-line momentum. Combine with gross margin trend to
assess quality of growth (margin-accretive vs margin-dilutive).

## Status Thresholds

| Status | Condition |
|--------|-----------|
| GREEN  | At or above benchmark |
| YELLOW | Within 5 percentage points below benchmark |
| RED    | More than 5 percentage points below benchmark |

## Notes

- All calculations should use the same currency denomination consistently.
- QoQ comparisons assume sequential quarters (Q1->Q2, Q2->Q3, Q3->Q4).
- When prior period data is unavailable, omit growth metrics rather than estimating.
