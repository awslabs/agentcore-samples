#!/usr/bin/env python3
import aws_cdk as cdk
from nag_suppressions import apply_nag_suppressions
from weather_agent_stack import WeatherAgentStack

app = cdk.App()
stack = WeatherAgentStack(app, "WeatherAgentDemo")
apply_nag_suppressions(stack)

app.synth()
