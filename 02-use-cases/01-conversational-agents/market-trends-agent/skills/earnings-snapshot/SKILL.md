---
name: earnings-snapshot
description: Generate a concise earnings and fundamental snapshot for a stock, covering valuation, recent earnings news, and analyst positioning
allowed-tools:
  - get_stock_data
  - search_news
---

# Earnings Snapshot Skill

Use this skill when a user asks about a company's earnings, valuation metrics, fundamentals, whether a stock is cheap or expensive, or how recent results compared to expectations.

## Required workflow

1. Retrieve stock data using `get_stock_data` for the requested symbol.
2. Search for earnings-related news using `search_news` with the stock's sector.
3. Extract the following fundamental metrics from stock data:
   - P/E ratio (compare to sector average: Technology ~34x, Healthcare ~22x, Financials ~13x, Energy ~14x, Consumer Discretionary ~30x, Consumer Staples ~25x)
   - Dividend yield (0% = growth stock; >2% = income stock)
   - Market cap tier (Mega >$1T, Large $100B-$1T, Mid $10B-$100B)
4. Assess valuation:
   - P/E > 1.3× sector average → **Premium** (growth priced in)
   - P/E within 0.7×–1.3× sector average → **Fair Value**
   - P/E < 0.7× sector average → **Discount** (value opportunity or value trap)
5. Identify the most relevant earnings headline from news results.
6. Provide a 2-sentence earnings outlook.

## Output Format

```
Earnings Snapshot: {SYMBOL} — {Company Name}
  Price         : ${price}
  P/E Ratio     : {pe_ratio}x  ({Premium | Fair Value | Discount} vs. {sector} avg {sector_avg}x)
  Dividend Yield: {yield}%
  Market Cap    : ${cap}  ({tier})
  Earnings News : {top headline}
  Outlook       : {2-sentence assessment}
```
