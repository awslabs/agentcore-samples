"""Benchmark: Semantic Tool Search vs Load All Tools at varying tool counts.

Compares token usage and latency between two approaches:
1. Load all tools — passes every tool definition to the LLM
2. Semantic search — uses AgentCoreToolSearchPlugin to load only relevant tools

Requires:
    AWS_PROFILE or credentials configured
    BEDROCK_TEST_REGION (default: us-east-1)
    GATEWAY_ROLE_ARN (optional — auto-provisions if not set)
    GATEWAY_LAMBDA_ARN (optional — auto-provisions if not set)

Usage:
    python benchmarks/tool_search_scaling_benchmark.py
    python benchmarks/tool_search_scaling_benchmark.py --profile genai-demo-admin --region us-east-1
    python benchmarks/tool_search_scaling_benchmark.py --tool-counts 10 50 100 200
"""

import argparse
import io
import json
import logging
import os
import time
import zipfile
from dataclasses import dataclass, field

import boto3

from bedrock_agentcore.gateway.client import GatewayClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Infrastructure constants
_ROLE_NAME = "bench-test-gateway-role"
_LAMBDA_NAME = "bench-test-lambda"
_BENCHMARK_PREFIX = "bench-scaling"

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole"},
        {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"},
    ],
}


@dataclass
class BenchmarkResult:
    """Result for a single benchmark run."""

    tool_count: int
    approach: str  # "all_tools" or "semantic_search"
    latency_seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    tools_loaded: int = 0


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results."""

    results: list[BenchmarkResult] = field(default_factory=list)

    def add(self, result: BenchmarkResult):
        self.results.append(result)

    def print_report(self):
        print("\n" + "=" * 80)
        print("BENCHMARK RESULTS: Semantic Tool Search vs Load All Tools")
        print("=" * 80)
        print(f"{'Tools':>6} | {'Approach':<16} | {'Latency (s)':>11} | {'Input Tokens':>12} | "
              f"{'Output Tokens':>13} | {'Tools Loaded':>12}")
        print("-" * 80)
        for r in sorted(self.results, key=lambda x: (x.tool_count, x.approach)):
            print(f"{r.tool_count:>6} | {r.approach:<16} | {r.latency_seconds:>11.2f} | "
                  f"{r.input_tokens:>12} | {r.output_tokens:>13} | {r.tools_loaded:>12}")
        print("=" * 80)

        # Print savings summary
        print("\nSAVINGS SUMMARY:")
        print("-" * 60)
        tool_counts = sorted(set(r.tool_count for r in self.results))
        for tc in tool_counts:
            all_tools = next((r for r in self.results if r.tool_count == tc and r.approach == "all_tools"), None)
            semantic = next((r for r in self.results if r.tool_count == tc and r.approach == "semantic_search"), None)
            if all_tools and semantic:
                token_savings = all_tools.input_tokens - semantic.input_tokens
                token_pct = (token_savings / all_tools.input_tokens * 100) if all_tools.input_tokens else 0
                latency_savings = all_tools.latency_seconds - semantic.latency_seconds
                print(f"  {tc} tools: {token_savings:,} fewer input tokens ({token_pct:.1f}% reduction), "
                      f"{latency_savings:.2f}s faster")
        print()


def _generate_tool_definitions(count: int) -> list[dict]:
    """Generate synthetic tool definitions for benchmarking."""
    categories = [
        ("weather", "Get weather information for {location}", ["location"]),
        ("email", "Send an email about {topic} to {recipient}", ["topic", "recipient"]),
        ("calendar", "Schedule a {event_type} meeting", ["event_type", "date"]),
        ("database", "Query {table} database records", ["table", "query"]),
        ("file", "Manage {operation} on files in {directory}", ["operation", "directory"]),
        ("analytics", "Generate {report_type} analytics report", ["report_type", "timeframe"]),
        ("notification", "Send {channel} notification to {team}", ["channel", "team"]),
        ("deployment", "Deploy {service} to {environment}", ["service", "environment"]),
        ("monitoring", "Check {metric} metrics for {service}", ["metric", "service"]),
        ("security", "Run {scan_type} security scan on {target}", ["scan_type", "target"]),
    ]

    tools = []
    for i in range(count):
        cat_idx = i % len(categories)
        category, desc_template, params = categories[cat_idx]
        variant = i // len(categories)

        name = f"{category}_tool_{variant}" if variant > 0 else f"{category}_tool"
        description = f"{desc_template} (variant {variant})" if variant > 0 else desc_template

        properties = {}
        for p in params:
            properties[p] = {"type": "string", "description": f"The {p} parameter"}

        tools.append({
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": params,
            },
        })

    return tools


def _get_lambda_zip() -> bytes:
    """Create a minimal Lambda zip for benchmarking."""
    code = '''
import json

def lambda_handler(event, context):
    body = event.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)
    method = body.get("method", "")
    request_id = body.get("id")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "bench-mcp-server", "version": "1.0.0"}}
    elif method == "notifications/initialized":
        return {"statusCode": 200, "body": ""}
    elif method == "tools/list":
        result = {"tools": []}
    elif method == "tools/call":
        name = body.get("params", {}).get("name", "unknown")
        result = {"content": [{"type": "text", "text": f"Called {name}"}]}
    else:
        return {"statusCode": 200, "body": json.dumps({"jsonrpc": "2.0", "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}})}
    return {"statusCode": 200, "body": json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})}
'''
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", code)
    return buf.getvalue()


class RegexIntentProvider:
    """Lightweight intent provider that extracts keywords via regex — no LLM call."""

    # Stop words to filter out
    _STOP_WORDS = frozenset([
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above", "below",
        "between", "out", "off", "over", "under", "again", "further", "then",
        "once", "here", "there", "when", "where", "why", "how", "all", "each",
        "every", "both", "few", "more", "most", "other", "some", "such", "no",
        "nor", "not", "only", "own", "same", "so", "than", "too", "very",
        "just", "because", "if", "or", "and", "but", "what", "which", "who",
        "whom", "this", "that", "these", "those", "i", "me", "my", "myself",
        "we", "our", "you", "your", "he", "him", "she", "her", "it", "its",
        "they", "them", "their", "please", "tell", "get", "give", "make",
    ])

    def __init__(self, max_keywords: int = 5):
        self._max_keywords = max_keywords

    def derive_intent(self, messages: list[dict], model=None) -> str:
        """Extract meaningful keywords from the last user message."""
        import re

        if not messages:
            return ""

        # Get last user message text
        last_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    last_msg = " ".join(
                        b.get("text", "") for b in content if isinstance(b, dict) and "text" in b
                    )
                break

        if not last_msg:
            return ""

        # Extract words, filter stop words, take top N
        words = re.findall(r'\b[a-zA-Z]{3,}\b', last_msg.lower())
        keywords = [w for w in words if w not in self._STOP_WORDS]
        return " ".join(keywords[:self._max_keywords])


class InfraManager:
    """Manages AWS infrastructure for benchmarking."""

    def __init__(self, session, region: str):
        self.session = session
        self.region = region
        self.iam = session.client("iam")
        self.lambda_client = session.client("lambda", region_name=region)
        self.gw_client = GatewayClient(region_name=region)
        self.role_arn = None
        self.lambda_arn = None
        self.gateway_id = None
        self.target_id = None
        self._provisioned = False

    def setup(self, role_arn: str = None, lambda_arn: str = None):
        """Set up or reuse infrastructure."""
        if role_arn and lambda_arn:
            self.role_arn = role_arn
            self.lambda_arn = lambda_arn
            return

        self._provisioned = True
        self.role_arn = self._ensure_role()
        self.lambda_arn = self._ensure_lambda()
        self._attach_invoke_policy()

    def create_gateway_with_tools(self, tool_count: int) -> tuple[str, str]:
        """Create a gateway with N tools registered. Returns (gateway_id, target_id)."""
        tools = _generate_tool_definitions(tool_count)
        prefix = f"{_BENCHMARK_PREFIX}-{tool_count}t-{int(time.time())}"

        gw = self.gw_client.create_gateway_and_wait(
            name=f"{prefix}-gw",
            roleArn=self.role_arn,
            authorizerType="NONE",
            protocolType="MCP",
            protocolConfiguration={"mcp": {"searchType": "SEMANTIC"}},
        )
        gateway_id = gw["gatewayId"]
        logger.info("Created gateway %s with %d tools", gateway_id, tool_count)

        target = self.gw_client.create_gateway_target_and_wait(
            gatewayIdentifier=gateway_id,
            name=f"{prefix}-target",
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": self.lambda_arn,
                        "toolSchema": {"inlinePayload": tools},
                    }
                },
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        target_id = target["targetId"]

        # Wait for semantic indexing
        logger.info("Waiting 60s for semantic indexing...")
        time.sleep(60)

        return gateway_id, target_id

    def delete_gateway(self, gateway_id: str, target_id: str):
        """Clean up a gateway and its target."""
        try:
            self.gw_client.delete_gateway_target_and_wait(gatewayIdentifier=gateway_id, targetId=target_id)
        except Exception as e:
            logger.warning("Failed to delete target: %s", e)
        try:
            self.gw_client.delete_gateway_and_wait(gatewayIdentifier=gateway_id)
        except Exception as e:
            logger.warning("Failed to delete gateway: %s", e)

    def teardown(self):
        """Clean up provisioned infrastructure."""
        if not self._provisioned:
            return
        try:
            self.lambda_client.delete_function(FunctionName=_LAMBDA_NAME)
        except Exception:
            pass
        try:
            self.iam.delete_role_policy(RoleName=_ROLE_NAME, PolicyName="lambda-invoke")
        except Exception:
            pass
        try:
            self.iam.detach_role_policy(RoleName=_ROLE_NAME,
                                       PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        except Exception:
            pass
        try:
            self.iam.delete_role(RoleName=_ROLE_NAME)
        except Exception:
            pass

    def _ensure_role(self) -> str:
        try:
            return self.iam.get_role(RoleName=_ROLE_NAME)["Role"]["Arn"]
        except self.iam.exceptions.NoSuchEntityException:
            pass
        resp = self.iam.create_role(
            RoleName=_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
            Description="Benchmark role for gateway tests",
        )
        self.iam.attach_role_policy(
            RoleName=_ROLE_NAME,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
        time.sleep(10)
        return resp["Role"]["Arn"]

    def _ensure_lambda(self) -> str:
        try:
            return self.lambda_client.get_function(FunctionName=_LAMBDA_NAME)["Configuration"]["FunctionArn"]
        except self.lambda_client.exceptions.ResourceNotFoundException:
            pass
        resp = self.lambda_client.create_function(
            FunctionName=_LAMBDA_NAME, Runtime="python3.10", Role=self.role_arn,
            Handler="lambda_function.lambda_handler", Code={"ZipFile": _get_lambda_zip()},
            Timeout=30, Description="Benchmark Lambda",
        )
        waiter = self.lambda_client.get_waiter("function_active_v2")
        waiter.wait(FunctionName=_LAMBDA_NAME)
        return resp["FunctionArn"]

    def _attach_invoke_policy(self):
        policy = {"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": self.lambda_arn}
        ]}
        self.iam.put_role_policy(RoleName=_ROLE_NAME, PolicyName="lambda-invoke",
                                PolicyDocument=json.dumps(policy))


def _count_tokens_for_tools(tools: list[dict]) -> int:
    """Estimate input tokens for tool definitions (roughly 4 chars per token)."""
    text = json.dumps(tools)
    return len(text) // 4


def benchmark_all_tools(tool_count: int, tools: list[dict], region: str, gateway_id: str) -> BenchmarkResult:
    """Benchmark the 'load all tools' approach — uses real MCP client with list_tools."""
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from strands.tools.mcp import MCPClient

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", region_name=region)
    endpoint = f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"

    mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
        endpoint=endpoint, aws_region=region, aws_service="bedrock-agentcore",
    ))

    start = time.time()
    agent = Agent(model=model, tools=[mcp_client],
                  system_prompt="You are helpful. Use tools when needed.")
    response = agent("What is the weather in Seattle?")
    elapsed = time.time() - start

    input_tokens = _count_tokens_for_tools(tools)

    return BenchmarkResult(
        tool_count=tool_count,
        approach="all_tools",
        latency_seconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=0,
        tools_loaded=tool_count,
    )


def benchmark_semantic_search(tool_count: int, gateway_id: str, region: str) -> BenchmarkResult:
    """Benchmark the semantic search approach."""
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from strands.tools.mcp import MCPClient

    from bedrock_agentcore.gateway.integrations.strands.plugins import AgentCoreToolSearchPlugin

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", region_name=region)
    endpoint = f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"

    mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
        endpoint=endpoint, aws_region=region, aws_service="bedrock-agentcore",
    ))

    start = time.time()
    with mcp_client:
        plugin = AgentCoreToolSearchPlugin(mcp_client=mcp_client)
        agent = Agent(model=model, tools=[], plugins=[plugin],
                      system_prompt="You are helpful. Use tools when needed.")
        response = agent("What is the weather in Seattle?")
    elapsed = time.time() - start

    tools_loaded = len(plugin._loaded_tool_names)
    # Estimate tokens: each loaded tool is roughly the same size as the generated ones
    all_tools = _generate_tool_definitions(tool_count)
    per_tool_tokens = _count_tokens_for_tools(all_tools) // tool_count if tool_count > 0 else 0
    input_tokens = per_tool_tokens * tools_loaded

    return BenchmarkResult(
        tool_count=tool_count,
        approach="semantic_search",
        latency_seconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=0,
        tools_loaded=tools_loaded,
    )


def benchmark_regex_search(tool_count: int, gateway_id: str, region: str) -> BenchmarkResult:
    """Benchmark semantic search with regex-based intent provider (no LLM for intent)."""
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    from strands import Agent
    from strands.models.bedrock import BedrockModel
    from strands.tools.mcp import MCPClient

    from bedrock_agentcore.gateway.integrations.strands.plugins import AgentCoreToolSearchPlugin

    model = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0", region_name=region)
    endpoint = f"https://{gateway_id}.gateway.bedrock-agentcore.{region}.amazonaws.com/mcp"

    mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
        endpoint=endpoint, aws_region=region, aws_service="bedrock-agentcore",
    ))

    regex_provider = RegexIntentProvider(max_keywords=5)

    start = time.time()
    with mcp_client:
        plugin = AgentCoreToolSearchPlugin(mcp_client=mcp_client, intent_provider=regex_provider)
        agent = Agent(model=model, tools=[], plugins=[plugin],
                      system_prompt="You are helpful. Use tools when needed.")
        response = agent("What is the weather in Seattle?")
    elapsed = time.time() - start

    tools_loaded = len(plugin._loaded_tool_names)
    all_tools = _generate_tool_definitions(tool_count)
    per_tool_tokens = _count_tokens_for_tools(all_tools) // tool_count if tool_count > 0 else 0
    input_tokens = per_tool_tokens * tools_loaded

    return BenchmarkResult(
        tool_count=tool_count,
        approach="regex_search",
        latency_seconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=0,
        tools_loaded=tools_loaded,
    )


def main():
    parser = argparse.ArgumentParser(description="Benchmark: Semantic Tool Search vs Load All Tools")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--profile", default=None, help="AWS profile")
    parser.add_argument("--tool-counts", nargs="+", type=int, default=[10, 50, 100, 200],
                        help="Tool counts to benchmark")
    parser.add_argument("--role-arn", default=os.environ.get("GATEWAY_ROLE_ARN"), help="IAM role ARN")
    parser.add_argument("--lambda-arn", default=os.environ.get("GATEWAY_LAMBDA_ARN"), help="Lambda ARN")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    # Set default profile for SDK clients that use boto3 directly
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile

    infra = InfraManager(session, args.region)
    infra.setup(role_arn=args.role_arn, lambda_arn=args.lambda_arn)

    suite = BenchmarkSuite()
    gateways_to_cleanup = []

    try:
        for tool_count in args.tool_counts:
            logger.info("=" * 60)
            logger.info("Benchmarking with %d tools", tool_count)
            logger.info("=" * 60)

            tools = _generate_tool_definitions(tool_count)

            # Create gateway for this tool count
            gateway_id, target_id = infra.create_gateway_with_tools(tool_count)
            gateways_to_cleanup.append((gateway_id, target_id))

            # Benchmark: Load all tools
            logger.info("Running 'all_tools' benchmark with %d tools...", tool_count)
            try:
                result = benchmark_all_tools(tool_count, tools, args.region, gateway_id)
                suite.add(result)
                logger.info("  all_tools: %.2fs, %d input tokens", result.latency_seconds, result.input_tokens)
            except Exception as e:
                logger.error("  all_tools benchmark failed: %s", e)

            # Benchmark: Semantic search
            logger.info("Running 'semantic_search' benchmark with %d tools...", tool_count)
            try:
                result = benchmark_semantic_search(tool_count, gateway_id, args.region)
                suite.add(result)
                logger.info("  semantic_search: %.2fs, %d input tokens, %d tools loaded",
                            result.latency_seconds, result.input_tokens, result.tools_loaded)
            except Exception as e:
                logger.error("  semantic_search benchmark failed: %s", e)

            # Benchmark: Regex-based search (no LLM for intent)
            logger.info("Running 'regex_search' benchmark with %d tools...", tool_count)
            try:
                result = benchmark_regex_search(tool_count, gateway_id, args.region)
                suite.add(result)
                logger.info("  regex_search: %.2fs, %d input tokens, %d tools loaded",
                            result.latency_seconds, result.input_tokens, result.tools_loaded)
            except Exception as e:
                logger.error("  regex_search benchmark failed: %s", e)

    finally:
        # Clean up gateways
        for gw_id, tgt_id in gateways_to_cleanup:
            infra.delete_gateway(gw_id, tgt_id)
        infra.teardown()

    suite.print_report()

    # Save results to JSON
    output_path = os.path.join(os.path.dirname(__file__), "results", "benchmark_results.json")
    with open(output_path, "w") as f:
        json.dump([{
            "tool_count": r.tool_count,
            "approach": r.approach,
            "latency_seconds": r.latency_seconds,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "tools_loaded": r.tools_loaded,
        } for r in suite.results], f, indent=2)
    logger.info("Results saved to %s", output_path)


if __name__ == "__main__":
    main()
