#!/usr/bin/env python3
import aws_cdk as cdk
from nag_suppressions import apply_nag_suppressions
from basic_runtime_stack import BasicRuntimeStack

app = cdk.App()
stack = BasicRuntimeStack(app, "BasicAgentDemo")
apply_nag_suppressions(stack)

app.synth()
