"""
Configuration module for AWS re:Invent 2025 AIML301 Workshop
AgentCore SRE UseCase - Centralized configuration

Static configuration values as module-level variables.
Customers can import these directly and see where values are coming from.

Usage:
    from lab_helpers.config import AWS_REGION, MODEL_ID, WORKSHOP_NAME
    from lab_helpers import config

    print(config.AWS_REGION)
    print(config.MODEL_ID)
"""

# ============================================================================
# AWS Configuration
# ============================================================================
AWS_REGION = "us-west-2"  # Changed from us-west-2 to match working deployment
AWS_PROFILE = None

# ============================================================================
# Bedrock Model Configuration
# ============================================================================
# Claude Sonnet 4 via Global CRIS (Cross-Region Inference Server)
# Model ID: global.anthropic.claude-sonnet-4-20250514-v1:0
# - 200K context window
# - Released: May 22, 2025
# MODEL_ID = "global.anthropic.claude-sonnet-4-20250514-v1:0"
MODEL_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0"
# ============================================================================
# Workshop Configuration
# ============================================================================
WORKSHOP_NAME = "aiml301_sre_agentcore"
