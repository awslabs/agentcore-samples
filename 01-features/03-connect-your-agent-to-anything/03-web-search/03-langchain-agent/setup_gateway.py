"""Set up AgentCore gateway with Web Search Tool — delegates to shared utility.

Usage:
python setup_gateway.py
python setup_gateway.py --gateway-name my-gateway
python setup_gateway.py --region us-east-1
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.gateway_setup import main

if __name__ == "__main__":
    main()
