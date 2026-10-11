#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
CDK App — Text-to-SQL with Amazon Bedrock AgentCore

Deploys the complete infrastructure:
- Data Lake (S3) + Glue Data Catalog (from config/tables.yaml)
- Backend (Lambda + API Gateway)
- Frontend (S3 + CloudFront)
- Amazon Bedrock Guardrails
"""

import os
import aws_cdk as cdk
from stack import TextToSQLStack

app = cdk.App()

# Configuration — edit these values or set environment variables
PROJECT_NAME = os.environ.get("PROJECT_NAME", "my-company")
AWS_ACCOUNT = os.environ.get("AWS_ACCOUNT_ID", "123456789012")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

env = cdk.Environment(account=AWS_ACCOUNT, region=AWS_REGION)

TextToSQLStack(
    app,
    f"{PROJECT_NAME}-TextToSQL",
    project_name=PROJECT_NAME,
    env=env,
    description=f"Text-to-SQL GenAI Stack for {PROJECT_NAME}",
)

cdk.Tags.of(app).add("Project", f"{PROJECT_NAME}-TextToSQL")
cdk.Tags.of(app).add("ManagedBy", "CDK")

app.synth()
