#!/usr/bin/env python3
"""CLI demo for AgentCore Gateway token exchange at request interceptor."""

import argparse
import base64
import json
import subprocess
import sys
import time

import bedrock_models
import requests
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client


# -- ANSI styling -------------------------------------------------------------

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
UNDERLINE = "\033[4m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

BG_BLACK = "\033[40m"

OK = f"{GREEN}{BOLD}  OK  {RESET}"
FAIL = f"{RED}{BOLD} FAIL {RESET}"


def header(text: str) -> None:
    width = 72
    print()
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")
    print(f"{CYAN}{BOLD}  {text}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")
    print()


def section(text: str) -> None:
    print(f"\n{BLUE}{BOLD}--- {text} ---{RESET}\n")


def kv(key: str, value: str, mask: bool = False) -> None:
    display = value[:12] + "..." if mask and len(value) > 12 else value
    print(f"  {DIM}{key:<32}{RESET} {WHITE}{display}{RESET}")


def status(label: str, ok: bool) -> None:
    tag = OK if ok else FAIL
    print(f"  [{tag}] {label}")


def step(n: int, text: str) -> None:
    print(f"{MAGENTA}{BOLD}[Step {n}]{RESET} {text}")


def jwt_decode_payload(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += "=" * padding
    return json.loads(base64.urlsafe_b64decode(payload))


def jwt_client_id(token: str) -> str:
    claims = jwt_decode_payload(token)
    return claims.get("client_id", "unknown")


# -- Token provider ------------------------------------------------------------


class CognitoM2MTokenProvider:
    """Acquires and caches a Cognito client_credentials token, refreshing on demand."""

    def __init__(
        self,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        resource_server_id: str,
        verbose: bool = False,
    ):
        self._token_endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._resource_server_id = resource_server_id
        self._verbose = verbose
        self._access_token: str | None = None

    def _fetch_token(self) -> str:
        if self._verbose:
            kv("Token endpoint", self._token_endpoint)
            kv("Client ID", self._client_id)
            kv("Grant type", "client_credentials")
            kv(
                "Scopes",
                f"{self._resource_server_id}/read {self._resource_server_id}/write",
            )

        credentials = f"{self._client_id}:{self._client_secret}"
        encoded = base64.b64encode(credentials.encode()).decode()
        rs = self._resource_server_id

        resp = requests.post(
            self._token_endpoint,
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": f"{rs}/read {rs}/write",
            },
            timeout=10,
        )

        if resp.status_code != 200:
            if self._verbose:
                status("Token request", False)
                print(f"  {RED}{resp.status_code}: {resp.text}{RESET}")
            raise RuntimeError(f"Cognito token request failed: {resp.status_code} {resp.text}")

        token_data = resp.json()
        token = token_data["access_token"]
        self._access_token = token

        if self._verbose:
            status("Token request", True)
            kv("Token type", token_data.get("token_type", "unknown"))
            kv("Expires in", f"{token_data.get('expires_in', '?')}s")
            kv("Access token", token, mask=True)

            section("JWT claims")
            claims = jwt_decode_payload(token)
            for k in ("iss", "client_id", "token_use", "scope", "exp"):
                if k in claims:
                    val = claims[k]
                    if k == "exp":
                        val = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(val))
                    kv(k, str(val))

        return token

    @property
    def token(self) -> str:
        if self._access_token is None:
            self._fetch_token()
        return self._access_token  # type: ignore[return-value]

    def refresh(self) -> str:
        if self._verbose:
            print(f"  {YELLOW}Acquiring Cognito M2M token...{RESET}")
        self._access_token = None
        return self._fetch_token()


# -- Terraform output loader --------------------------------------------------


def load_terraform_outputs(tf_dir: str) -> dict:
    result = subprocess.run(
        ["terraform", "output", "-json"],
        cwd=tf_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"{RED}Failed to read terraform outputs:{RESET}")
        print(result.stderr)
        sys.exit(1)
    raw = json.loads(result.stdout)
    return {k: v["value"] for k, v in raw.items()}


# -- Demo steps ----------------------------------------------------------------


def demo_config(cfg: dict) -> None:
    section("Infrastructure")
    kv("Gateway URL", cfg["gateway_url"])
    kv("Gateway ID", cfg["gateway_id"])
    kv("API Gateway URL", cfg["api_gateway_url"])
    kv("Interceptor Lambda", cfg["interceptor_lambda_arn"].split(":")[-1])

    section("Cognito clients (two separate identities)")
    kv("Gateway client  (inbound)", cfg["cognito_gateway_client_id"])
    kv("Downstream client (API GW)", cfg["cognito_downstream_client_id"])
    kv("Resource Server", cfg["cognito_resource_server_id"])
    kv("Token Endpoint", cfg["cognito_token_endpoint"])


def demo_direct_api_call(cfg: dict, access_token: str) -> None:
    step(2, "Proving the gateway token is rejected by the API Gateway directly")

    url = f"{cfg['api_gateway_url']}/posts"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"title": "Direct call", "body": "This should fail", "userId": 1},
        timeout=10,
    )

    rejected = resp.status_code == 401
    status(
        f"API Gateway returned {resp.status_code} (expected 401)",
        rejected,
    )
    if rejected:
        print(f"  {DIM}The gateway client token was correctly rejected by the API Gateway.{RESET}")
    else:
        print(f"  {YELLOW}Unexpected status. Response: {resp.text[:200]}{RESET}")


def demo_downstream_api_call(cfg: dict) -> None:
    step(3, "Proving the downstream client token IS accepted by the API Gateway")

    downstream_provider = CognitoM2MTokenProvider(
        token_endpoint=cfg["cognito_token_endpoint"],
        client_id=cfg["cognito_downstream_client_id"],
        client_secret=cfg["cognito_downstream_client_secret"],
        resource_server_id=cfg["cognito_resource_server_id"],
        # verbose=True,
    )
    downstream_token = downstream_provider.refresh()
    kv("Downstream client_id", jwt_client_id(downstream_token))

    url = f"{cfg['api_gateway_url']}/posts"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {downstream_token}",
            "Content-Type": "application/json",
        },
        json={
            "title": "Downstream direct call",
            "body": "This should succeed",
            "userId": 1,
        },
        timeout=10,
    )

    accepted = resp.status_code in (200, 201)
    status(
        f"API Gateway returned {resp.status_code} (expected 200/201)",
        accepted,
    )
    if accepted:
        print(f"  {DIM}The downstream client token was accepted by the API Gateway.{RESET}")
        try:
            print(f"  {GREEN}{json.dumps(resp.json(), indent=2)}{RESET}")
        except (json.JSONDecodeError, ValueError):
            print(f"  {GREEN}{resp.text[:300]}{RESET}")
    else:
        print(f"  {YELLOW}Unexpected status. Response: {resp.text[:200]}{RESET}")


def demo_gateway_tools(cfg: dict, token_provider: CognitoM2MTokenProvider) -> None:
    step(4, "Connecting to AgentCore Gateway via MCP")

    gateway_url = cfg["gateway_url"]

    def create_transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token_provider.token}"},
        )

    client = MCPClient(create_transport)

    with client:
        tools = client.list_tools_sync()
        status("MCP handshake", True)
        kv("Tools discovered", str(len(tools)))

        section("Available tools")
        for i, tool in enumerate(tools):
            name = tool.tool_name
            desc = getattr(tool, "description", "") or ""
            desc = desc[:60] + "..." if len(desc) > 60 else desc
            print(f"  {YELLOW}{i + 1}.{RESET} {BOLD}{name}{RESET}  {DIM}{desc}{RESET}")

        step(
            5,
            "Calling createPost through the gateway (interceptor exchanges token)",
        )

        if tools:
            tool_name = tools[0].tool_name
            arguments = {
                "title": "Hello from the CLI demo",
                "body": "This post was created through the AgentCore Gateway with token exchange.",
                "userId": 1,
            }
            print(f"  {DIM}Tool:      {tool_name}{RESET}")
            print(f"  {DIM}Arguments: {json.dumps(arguments, indent=None)}{RESET}")

            result = client.call_tool_sync(
                tool_use_id="demo-call-001",
                name=tool_name,
                arguments=arguments,
            )

            content_text = result["content"][0]["text"] if result.get("content") else str(result)
            status("Tool invocation via gateway", True)

            section("Tool response")
            try:
                parsed = json.loads(content_text)
                print(f"  {GREEN}{json.dumps(parsed, indent=2)}{RESET}")
            except (json.JSONDecodeError, TypeError):
                print(f"  {GREEN}{content_text[:500]}{RESET}")

            print()
            print(f"  {CYAN}The interceptor exchanged the gateway-client token for a{RESET}")
            print(f"  {CYAN}downstream-client token before forwarding to the API Gateway.{RESET}")
            print(f"  {DIM}Gateway client:    {cfg['cognito_gateway_client_id']}{RESET}")
            print(f"  {DIM}Downstream client: {cfg['cognito_downstream_client_id']}{RESET}")
        else:
            print(f"  {YELLOW}No tools available to invoke{RESET}")


def demo_agent(cfg: dict, prompt: str) -> None:
    step(6, "Running Strands agent with M2M token acquisition")

    gateway_url = cfg["gateway_url"]
    token_provider = CognitoM2MTokenProvider(
        token_endpoint=cfg["cognito_token_endpoint"],
        client_id=cfg["cognito_gateway_client_id"],
        client_secret=cfg["cognito_gateway_client_secret"],
        resource_server_id=cfg["cognito_resource_server_id"],
        # verbose=True,
    )

    # -- 5a: attempt without a token to demonstrate the 401 -----------------
    section("Attempting gateway connection without a token")
    resp = requests.post(
        gateway_url,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        json={"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}},
        timeout=10,
    )
    is_401 = resp.status_code == 401
    status(f"Gateway returned {resp.status_code} (expected 401)", is_401)
    if is_401:
        print(f"  {DIM}No token provided -- gateway rejected the request.{RESET}")
    else:
        print(f"  {YELLOW}Unexpected: {resp.text[:200]}{RESET}")

    # -- 5b: acquire token via M2M and connect ------------------------------
    section("Acquiring M2M token via client_credentials flow")
    access_token = token_provider.refresh()

    print()
    print(f"  {YELLOW}client_id = {jwt_client_id(access_token)}{RESET}")
    print(f"  {DIM}This is the GATEWAY client. The API Gateway will NOT accept this token.{RESET}")
    print(f"  {DIM}The interceptor will exchange it for a DOWNSTREAM client token.{RESET}")

    # -- 5c: connect and run agent ------------------------------------------
    section("Connecting agent to gateway")

    def create_transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token_provider.token}"},
        )

    client = MCPClient(create_transport)
    modelId = bedrock_models.global_model_id(bedrock_models.Models.ANTHROPIC_CLAUDE_SONNET_4_6)
    model = BedrockModel(model_id=modelId, temperature=0.7)

    with client:
        tools = client.list_tools_sync()
        status("MCP handshake with token", True)

        agent = Agent(model=model, tools=tools)
        kv("Agent model", modelId)
        kv("Agent tools", ", ".join(t.tool_name for t in tools))

        section(f"Agent prompt: {WHITE}{prompt}{RESET}")
        print(f"  {DIM}Thinking...{RESET}")

        response = agent(prompt)

        section("Agent response")
        print(f"  {GREEN}{str(response)}{RESET}")


# -- Main ---------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Demo: AgentCore Gateway token exchange at request interceptor")
    parser.add_argument(
        "--tf-dir",
        default="terraform",
        help="Path to the terraform directory (default: terraform)",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Skip the Strands agent step",
    )
    parser.add_argument(
        "--prompt",
        default="Create a post titled 'Token Exchange Works' with a body explaining that the interceptor successfully exchanged the token.",
        help="Prompt to send to the Strands agent",
    )
    args = parser.parse_args()

    header("AgentCore Gateway - Token Exchange at Request Interceptor")

    print(f"{DIM}Loading terraform outputs from {args.tf_dir}...{RESET}")
    cfg = load_terraform_outputs(args.tf_dir)
    demo_config(cfg)

    # Single token provider used across all steps
    token_provider = CognitoM2MTokenProvider(
        token_endpoint=cfg["cognito_token_endpoint"],
        client_id=cfg["cognito_gateway_client_id"],
        client_secret=cfg["cognito_gateway_client_secret"],
        resource_server_id=cfg["cognito_resource_server_id"],
        verbose=True,
    )

    step(1, "Acquiring inbound token using the GATEWAY client")
    access_token = token_provider.refresh()

    print()
    print(f"  {YELLOW}client_id = {jwt_client_id(access_token)}{RESET}")
    print(f"  {DIM}This is the GATEWAY client. The API Gateway will NOT accept this token.{RESET}")
    print(f"  {DIM}The interceptor will exchange it for a DOWNSTREAM client token.{RESET}")

    print()
    print(f"{CYAN}{BOLD}{'- ' * 36}{RESET}")
    print(f"{CYAN}  Two Cognito clients are in play:{RESET}")
    print(f"{CYAN}    1. Gateway client  - authenticates the caller to AgentCore Gateway{RESET}")
    print(f"{CYAN}    2. Downstream client - authenticates the call to the API Gateway{RESET}")
    print(f"{CYAN}  The interceptor Lambda exchanges (1) for (2) on every request.{RESET}")
    print(f"{CYAN}{BOLD}{'- ' * 36}{RESET}")
    print()

    demo_direct_api_call(cfg, access_token)
    demo_downstream_api_call(cfg)
    demo_gateway_tools(cfg, token_provider)

    if not args.skip_agent:
        demo_agent(cfg, args.prompt)

    header("Demo complete")


if __name__ == "__main__":
    main()
