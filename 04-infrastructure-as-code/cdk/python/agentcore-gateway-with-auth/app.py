#!/usr/bin/env python3
import os

import aws_cdk as cdk

from agentcore_gateway_with_auth.agentcore_gateway_with_auth_stack import AgentcoreGatewayWithAuthStack


app = cdk.App()
AgentcoreGatewayWithAuthStack(app, "AgentcoreGatewayWithAuthStack")

app.synth()
