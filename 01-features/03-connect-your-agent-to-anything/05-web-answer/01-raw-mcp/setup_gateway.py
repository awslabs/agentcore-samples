"""Set up an Amazon Bedrock AgentCore Gateway with the Web Answer Tool.

Documentation:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html

This script delegates to the shared setup utility.
After running this script, load credentials with `source .env.web-answer` to use
with the other web answer examples.

Usage:
    python setup_gateway.py
    python setup_gateway.py --gateway-name my-gateway
    python setup_gateway.py --region us-east-1
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.gateway_setup import main

if __name__ == "__main__":
    main()
