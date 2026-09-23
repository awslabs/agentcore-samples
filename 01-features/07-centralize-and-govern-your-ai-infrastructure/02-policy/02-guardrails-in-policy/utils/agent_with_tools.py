"""
Insurance Underwriting Agent with MCP Tools via AgentCore gateway.

Context manager for a Strands agent that connects to the gateway
authenticated with SigV4 (IAM auth, no Cognito required).
"""

import json
import os
from pathlib import Path
from typing import Generator

import boto3
import httpx
from botocore.auth import SigV4Auth as BotoSigV4Auth
from botocore.awsrequest import AWSRequest

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client


def load_config(config_path: str = "guardrail_config.json") -> dict:
    """Load guardrail configuration from guardrail_config.json."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}\nPlease run deploy.py first.")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class IAMAuth(httpx.Auth):
    """
    httpx Auth implementation that signs each request with SigV4 (AWS IAM).

    Unlike pre-computing headers once, this signs each HTTP request
    individually so that the timestamp and payload hash are correct
    for every call — required for SigV4 authentication.
    """

    def __init__(self, region: str, service: str = "bedrock-agentcore", profile: str = None):
        self.region = region
        self.service = service
        self._boto_session = boto3.Session(profile_name=profile, region_name=region)

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        creds = self._boto_session.get_credentials().get_frozen_credentials()
        body = request.content or b""
        aws_req = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=body,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ("content-length",)},
        )
        BotoSigV4Auth(creds, self.service, self.region).add_auth(aws_req)
        for key, value in aws_req.headers.items():
            request.headers[key] = value
        yield request


class AgentSession:
    """
    Context manager for the insurance underwriting agent session.

    Connects to the AgentCore gateway using SigV4 (IAM) authentication.
    The gateway enforces guardrail policies on tool calls.

    Usage:
        with AgentSession() as session:
            response = session.invoke(
                "Create application for US region with $500K coverage. "
                "Set the message field to: standard residential policy, no prior claims."
            )
    """

    def __init__(self, model_id: str = "us.amazon.nova-lite-v1:0", verbose: bool = True):
        self.model_id = model_id
        self.verbose = verbose
        self.mcp_client = None
        self.agent = None
        self.config = None

    def __enter__(self):
        self.config = load_config()
        region = self.config.get("region")
        gateway_url = self.config["gateway"]["gateway_url"]
        profile = self.config.get("aws_profile")

        os.environ["AWS_DEFAULT_REGION"] = region

        if self.verbose:
            print(f"  Gateway: {self.config['gateway']['gateway_id']}")
            print(f"  Region:  {region}")

        iam_auth = IAMAuth(region=region, service="bedrock-agentcore", profile=profile)

        bedrock_model = BedrockModel(model_id=self.model_id, streaming=True)
        self.mcp_client = MCPClient(
            lambda: streamablehttp_client(
                gateway_url,
                auth=iam_auth,
            )
        )
        self.mcp_client.__enter__()
        tools = self.mcp_client.list_tools_sync()

        if self.verbose:
            print(f"  Available tools ({len(tools)}):")
            for t in tools:
                print(f"    - {t.tool_name}")

        system_prompt = (
            "You are a helpful AI assistant for insurance underwriting operations. "
            "You have access to tools provided through the AgentCore gateway. "
            "The gateway enforces guardrail policies that block harmful content, "
            "prompt injection attempts, and sensitive information like SSNs or credit card numbers. "
            "Use only the tools provided. When a tool call is denied by policy, "
            "inform the user that the request was blocked by content safety guardrails."
        )
        self.agent = Agent(model=bedrock_model, tools=tools, system_prompt=system_prompt)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.mcp_client:
            try:
                self.mcp_client.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass

    def invoke(self, prompt: str) -> str:
        """Invoke the agent with a prompt and return the response text."""
        print(f"\n  Prompt: {prompt}")
        try:
            response = self.agent(prompt)
            if hasattr(response, "message"):
                content = response.message.get("content", "")
            else:
                content = response
            # Content is often a list of {type, text} items — flatten to string
            if isinstance(content, list):
                content = " ".join(
                    item.get("text", str(item)) if isinstance(item, dict) else str(item) for item in content
                )
            else:
                content = str(content)
            print(f"  Response: {content}")
            return content
        except Exception as exc:
            msg = f"Error: {exc}"
            print(f"  {msg}")
            return msg
