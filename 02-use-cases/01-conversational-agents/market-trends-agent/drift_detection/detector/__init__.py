"""Quality drift detection for the Market Trends Agent.

Reads the scores the agent's evaluators already produce, tracks each score stream
against its own history, and raises a CloudWatch alarm when a stream degrades and
stays degraded.

Modules:
  methods.py  EWMA, z-score, CUSUM, plus the persistence and latch gate. Pure.
  config.py   per-evaluator method selection, driven by measured score shape
  scores.py   reads evaluation results out of CloudWatch
  state.py    DynamoDB-backed baselines, warm-up progress, and drift latch
  handler.py  scheduled Lambda entry point
"""
