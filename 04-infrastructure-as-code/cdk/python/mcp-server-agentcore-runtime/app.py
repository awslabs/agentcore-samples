#!/usr/bin/env python3
import aws_cdk as cdk
from nag_suppressions import apply_nag_suppressions
from mcp_server_stack import MCPServerStack

app = cdk.App()
stack = MCPServerStack(app, "MCPServerDemo")
apply_nag_suppressions(stack)

app.synth()
