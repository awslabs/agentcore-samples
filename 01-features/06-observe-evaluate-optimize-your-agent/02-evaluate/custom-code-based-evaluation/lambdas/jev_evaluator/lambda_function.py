"""JEV-based AgentCore evaluator — Lambda entry point.

Re-exports handler.handler as lambda_handler so the Lambda function can be
configured with Handler=lambda_function.lambda_handler.

All evaluation logic lives in handler.py. See that file and evaluators.json
for the full evaluator definitions and Jev question format.
"""

from handler import handler as lambda_handler
