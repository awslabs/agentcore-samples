---
name: trend-analysis
description: Analyze price and volume trends for one or more stocks to determine momentum direction and key technical levels
allowed-tools:
  - get_stock_data
  - get_sector_data
  - search_news
---

# Trend Analysis Skill

Use this skill when a user asks about price trends, momentum, technical analysis, or whether a stock is trending up or down.

## Required workflow

1. Retrieve current stock data using `get_stock_data` for each symbol in the request.
2. Retrieve sector data for the stock's sector using `get_sector_data`.
3. Search for recent news affecting the stock using `search_news` with the stock's sector.
4. Evaluate momentum: classify the trend as **Uptrend**, **Downtrend**, or **Sideways** based on:
   - Daily price change direction and magnitude
   - Sector trend (outperforming / underperforming / neutral)
   - News sentiment (positive / negative / mixed)
5. Identify key support level: approximate as price × 0.95 (−5% from current).
6. Identify key resistance level: approximate as price × 1.05 (+5% from current).
7. Assign a confidence score (Low / Medium / High) based on alignment across price, sector, and news signals.
8. Output a structured Trend Analysis Report with: symbol, current price, trend direction, momentum signal, key levels, and confidence.

## Output Format

```
Trend Analysis: {SYMBOL}
  Price       : ${price}  ({change_pct:+.2f}% today)
  Trend       : {Uptrend | Downtrend | Sideways}
  Momentum    : {signal description}
  Support     : ${support}
  Resistance  : ${resistance}
  Confidence  : {Low | Medium | High}
  Summary     : {1-2 sentence rationale}
```
