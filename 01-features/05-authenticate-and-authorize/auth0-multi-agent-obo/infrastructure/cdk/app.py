# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""
AgentCore CDK Application Entry Point

This is the entry point for the AWS CDK application that defines the infrastructure
for the AgentCore Financial Services platform.

IMPORTANT: THIS IS A STUB IMPLEMENTATION FOR DEMONSTRATION PURPOSES ONLY.
This code does NOT create any actual cloud resources. It serves as a template
to show how the infrastructure would be organized when properly implemented.

To use this in a real implementation:
1. Install AWS CDK: npm install -g aws-cdk
2. Install Python dependencies: pip install -r requirements.txt
3. Configure AWS credentials
4. Uncomment the stack initializations below
5. Run: cdk synth (to see CloudFormation template)
6. Run: cdk deploy (to deploy to AWS)

Author: AgentCore Team
Version: 0.1.0 (STUB)
"""

import os
import sys

# Add project root to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from aws_cdk import App, Environment

# Import account guard for deployment safety
from scripts.aws_account_guard import (
    verify_account,
    AccountMismatchError,
    AccountVerificationError,
    EXPECTED_ACCOUNT_ID,
    EXPECTED_REGION,
)

# Import stack definitions (currently stubs)
# from agentcore_identity_stack import AgentCoreIdentityStack
# from agentcore_runtime_stack import AgentCoreRuntimeStack
# from agentcore_gateway_stack import AgentCoreGatewayStack


def main():
    """
    Main function to initialize and deploy CDK stacks.

    STUB IMPLEMENTATION: Currently does nothing as stacks are not implemented.
    When implemented, this would create and configure all infrastructure stacks.
    """

    # =========================================================================
    # ACCOUNT SAFETY CHECK - Prevents deployment to wrong account
    # =========================================================================
    print("=" * 80)
    print("ACCOUNT VERIFICATION")
    print("=" * 80)

    try:
        # This will raise AccountMismatchError if account doesn't match
        verify_account()
        print(f"[OK] Account verified: {EXPECTED_ACCOUNT_ID}")
        print(f"[OK] Region: {EXPECTED_REGION}")
    except AccountMismatchError as e:
        print(str(e))
        sys.exit(1)
    except AccountVerificationError as e:
        print(f"[ERROR] Account verification failed: {e}")
        sys.exit(1)

    print("=" * 80)
    print()

    # Initialize CDK app
    app = App()

    # Get environment configuration - USE VERIFIED VALUES
    account = EXPECTED_ACCOUNT_ID  # Always use verified account
    region = EXPECTED_REGION  # Always use expected region
    environment = app.node.try_get_context("environment") or "dev"

    # Define AWS environment with verified values
    Environment(account=account, region=region)

    print("=" * 80)
    print("AgentCore CDK Application - STUB MODE")
    print("=" * 80)
    print(f"Environment: {environment}")
    print(f"Account: {account}")
    print(f"Region: {region}")
    print()
    print("NOTE: This is a STUB implementation that does NOT create actual resources.")
    print("Stack definitions exist as templates only.")
    print("=" * 80)
    print()

    # -------------------------------------------------------------------------
    # STUB: Identity Stack
    # -------------------------------------------------------------------------
    # When implemented, this stack would create:
    # - JWT Authorizer for API Gateway with Auth0 configuration
    # - IAM roles for workload identities
    # - Cognito User Pool (alternative to Auth0)
    # - Identity federation configuration
    #
    # identity_stack = AgentCoreIdentityStack(
    #     app,
    #     f"AgentCoreIdentityStack-{environment}",
    #     env=env,
    #     environment=environment,
    #     auth0_domain=os.environ.get("AUTH0_DOMAIN"),
    #     auth0_audience=os.environ.get("AUTH0_AUDIENCE"),
    # )

    print("[ STUB ] Identity Stack - NOT CREATED")
    print("  - Would create: JWT Authorizer, IAM roles, Identity providers")
    print()

    # -------------------------------------------------------------------------
    # STUB: Runtime Stack
    # -------------------------------------------------------------------------
    # When implemented, this stack would create:
    # - Lambda functions for each agent (Coordinator, Profile, Accounts, etc.)
    # - ECS services for long-running agents
    # - DynamoDB tables for agent state
    # - S3 buckets for agent data
    # - EventBridge rules for agent orchestration
    #
    # runtime_stack = AgentCoreRuntimeStack(
    #     app,
    #     f"AgentCoreRuntimeStack-{environment}",
    #     env=env,
    #     environment=environment,
    #     identity_stack=identity_stack,  # Pass identity stack for dependencies
    # )

    print("[ STUB ] Runtime Stack - NOT CREATED")
    print("  - Would create: Lambda functions, ECS services, DynamoDB tables")
    print()

    # -------------------------------------------------------------------------
    # STUB: Gateway Stack
    # -------------------------------------------------------------------------
    # When implemented, this stack would create:
    # - API Gateway for external clients
    # - API Gateway for agent-to-agent communication
    # - VPC Link for private connectivity
    # - WAF rules for security
    # - CloudWatch dashboards for monitoring
    #
    # gateway_stack = AgentCoreGatewayStack(
    #     app,
    #     f"AgentCoreGatewayStack-{environment}",
    #     env=env,
    #     environment=environment,
    #     identity_stack=identity_stack,
    #     runtime_stack=runtime_stack,
    # )

    print("[ STUB ] Gateway Stack - NOT CREATED")
    print("  - Would create: API Gateway, VPC Link, WAF, CloudWatch")
    print()

    # -------------------------------------------------------------------------
    # Add common tags to all resources (when stacks are implemented)
    # -------------------------------------------------------------------------
    # Tags.of(app).add("Project", "AgentCore")
    # Tags.of(app).add("Environment", environment)
    # Tags.of(app).add("ManagedBy", "CDK")
    # Tags.of(app).add("CostCenter", "AgentCore-Financial-Services")

    print("=" * 80)
    print("CDK App Initialization Complete (STUB MODE)")
    print("To implement: Uncomment stack definitions and provide implementations")
    print("=" * 80)

    # Synthesize CloudFormation templates
    # In stub mode, this will generate empty/minimal templates
    app.synth()


if __name__ == "__main__":
    main()
