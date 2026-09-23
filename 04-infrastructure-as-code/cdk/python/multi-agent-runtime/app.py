#!/usr/bin/env python3
import aws_cdk as cdk
from nag_suppressions import apply_nag_suppressions
from multi_agent_stack import MultiAgentStack

app = cdk.App()
stack = MultiAgentStack(app, "MultiAgentDemo")
apply_nag_suppressions(stack)

app.synth()
