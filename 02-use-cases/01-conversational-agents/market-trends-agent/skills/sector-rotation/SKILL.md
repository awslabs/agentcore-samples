---
name: sector-rotation
description: Identify which market sectors to overweight or underweight based on current macro conditions and sector performance data
allowed-tools:
  - get_market_overview
  - get_sector_data
  - search_news
---

# Sector Rotation Skill

Use this skill when a user asks about sector allocation, where to rotate capital, which sectors to favor or avoid, or how to position a portfolio across sectors.

## Required workflow

1. Retrieve the overall market overview using `get_market_overview`.
2. Retrieve detailed data for the following sectors using `get_sector_data` for each: technology, healthcare, financials, energy, consumer_staples, consumer_discretionary.
3. Search for macro news using `search_news` with sector="macro" to understand the rate/GDP backdrop.
4. Rank sectors by today's performance and YTD trend direction.
5. Classify each sector as **Overweight**, **Neutral**, or **Underweight** based on:
   - Today's performance relative to the broad market
   - Outlook field from sector data
   - Macro backdrop (rate environment, growth signals)
6. Identify the top 2 sectors to overweight and the bottom 2 to underweight.
7. Provide a rotation rationale (2–3 sentences) explaining the macro driver.

## Output Format

```
Sector Rotation Recommendation
  Market backdrop : {brief macro summary}
  Overweight  (1) : {sector}  —  {reason}
  Overweight  (2) : {sector}  —  {reason}
  Neutral         : {sectors list}
  Underweight (1) : {sector}  —  {reason}
  Underweight (2) : {sector}  —  {reason}
  Rotation theme  : {1–2 sentence theme}
```
