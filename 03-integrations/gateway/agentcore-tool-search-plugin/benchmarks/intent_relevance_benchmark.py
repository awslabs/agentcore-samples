"""Benchmark: Intent Provider Search Relevance.

Measures how well different intent providers produce search queries that return
relevant tools from the AgentCore Gateway's semantic search.

Compares three approaches:
1. Raw message — pass the last user message directly as the search query
2. LLM intent — use StrandsIntentProvider to derive intent from conversation history
3. Regex intent — use RegexIntentProvider to extract keywords

Metrics:
- Precision: of the tools returned, how many match the expected category?
- Recall: does the expected tool category appear at all in results?

Test cases include:
- Simple single-message queries (baseline)
- Multi-turn conversations where latest message is ambiguous without context
- Conversations with topic shifts

Usage:
    python benchmarks/intent_relevance_benchmark.py --profile genai-demo-admin --region us-east-1
    python benchmarks/intent_relevance_benchmark.py --tool-count 200
"""

import argparse
import io
import json
import logging
import os
import re
import time
import zipfile
from dataclasses import dataclass, field

import boto3

from bedrock_agentcore.gateway.client import GatewayClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

_ROLE_NAME = "bench-relevance-gateway-role"
_LAMBDA_NAME = "bench-relevance-lambda"
_BENCHMARK_PREFIX = "bench-relevance"

TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"}, "Action": "sts:AssumeRole"},
        {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"},
    ],
}


# Test cases: (description, messages, expected_categories)
# expected_categories: list of tool category prefixes that should appear in search results
TEST_CASES = [
    # --- Simple single-message queries (baseline) ---
    {
        "name": "Simple: weather query",
        "type": "simple",
        "messages": [
            {"role": "user", "content": [{"text": "What is the weather in Seattle?"}]},
        ],
        "expected_categories": ["weather"],
    },
    {
        "name": "Simple: send email",
        "type": "simple",
        "messages": [
            {"role": "user", "content": [{"text": "Send an email to john@example.com about the project update"}]},
        ],
        "expected_categories": ["email"],
    },
    {
        "name": "Simple: deploy service",
        "type": "simple",
        "messages": [
            {"role": "user", "content": [{"text": "Deploy the payment service to production"}]},
        ],
        "expected_categories": ["deployment"],
    },
    # --- Multi-turn: ambiguous latest message needs context ---
    {
        "name": "Multi-turn: trip planning → weather",
        "type": "multi_turn",
        "messages": [
            {"role": "user", "content": [{"text": "I'm planning a trip to Tokyo next week"}]},
            {"role": "assistant", "content": [{"text": "That sounds exciting! How can I help you prepare?"}]},
            {"role": "user", "content": [{"text": "What should I pack?"}]},
        ],
        "expected_categories": ["weather"],
    },
    {
        "name": "Multi-turn: project discussion → email",
        "type": "multi_turn",
        "messages": [
            {"role": "user", "content": [{"text": "The quarterly review meeting went well yesterday"}]},
            {"role": "assistant", "content": [{"text": "Great to hear! What were the key outcomes?"}]},
            {"role": "user", "content": [{"text": "Can you let the team know about the new deadlines?"}]},
        ],
        "expected_categories": ["email", "notification"],
    },
    {
        "name": "Multi-turn: server issues → monitoring",
        "type": "multi_turn",
        "messages": [
            {"role": "user", "content": [{"text": "Users are reporting the app is slow today"}]},
            {"role": "assistant", "content": [{"text": "I can help investigate. What would you like me to check?"}]},
            {"role": "user", "content": [{"text": "Check if something is wrong"}]},
        ],
        "expected_categories": ["monitoring"],
    },
    {
        "name": "Multi-turn: database migration → database",
        "type": "multi_turn",
        "messages": [
            {"role": "user", "content": [{"text": "We need to migrate the user data to the new schema"}]},
            {"role": "assistant", "content": [{"text": "I can help with that. What's the first step?"}]},
            {"role": "user", "content": [{"text": "First, show me what we have currently"}]},
        ],
        "expected_categories": ["database"],
    },
    # --- Topic shift: latest message differs from earlier context ---
    {
        "name": "Topic shift: weather → email",
        "type": "topic_shift",
        "messages": [
            {"role": "user", "content": [{"text": "What's the weather like in London?"}]},
            {"role": "assistant", "content": [{"text": "It's currently 15°C and cloudy in London."}]},
            {"role": "user", "content": [{"text": "Actually, can you send a meeting invite to the London team?"}]},
        ],
        "expected_categories": ["email", "calendar"],
    },
    {
        "name": "Topic shift: analytics → security",
        "type": "topic_shift",
        "messages": [
            {"role": "user", "content": [{"text": "Show me the revenue report for Q3"}]},
            {"role": "assistant", "content": [{"text": "Here's the Q3 revenue summary..."}]},
            {"role": "user", "content": [{"text": "Wait, we had a security incident last week. Can you scan our systems?"}]},
        ],
        "expected_categories": ["security"],
    },
    {
        "name": "Topic shift: deployment → monitoring",
        "type": "topic_shift",
        "messages": [
            {"role": "user", "content": [{"text": "Deploy the new version of the auth service"}]},
            {"role": "assistant", "content": [{"text": "Deployed auth-service v2.3.1 to production."}]},
            {"role": "user", "content": [{"text": "How's it looking? Any errors?"}]},
        ],
        "expected_categories": ["monitoring"],
    },
]


@dataclass
class RelevanceResult:
    """Result for a single search relevance test."""

    test_name: str
    test_type: str
    approach: str
    search_query: str
    expected_categories: list[str]
    tools_returned: list[str]
    matching_tools: int
    total_tools_returned: int
    precision: float  # matching / total returned
    hit: bool  # at least one expected category found
    messages: list[dict] = field(default_factory=list)


@dataclass
class RelevanceSuite:
    """Collection of relevance results."""

    results: list[RelevanceResult] = field(default_factory=list)

    def add(self, result: RelevanceResult):
        self.results.append(result)

    def print_report(self):
        print("\n" + "=" * 100)
        print("INTENT PROVIDER SEARCH RELEVANCE RESULTS")
        print("=" * 100)

        approaches = sorted(set(r.approach for r in self.results))
        test_types = ["simple", "multi_turn", "topic_shift"]

        for approach in approaches:
            results = [r for r in self.results if r.approach == approach]
            hits = sum(1 for r in results if r.hit)
            avg_precision = sum(r.precision for r in results) / len(results) if results else 0
            print(f"\n  {approach}:")
            print(f"    Overall: {hits}/{len(results)} hits ({hits/len(results)*100:.0f}%), "
                  f"avg precision: {avg_precision:.1%}")

            for tt in test_types:
                tt_results = [r for r in results if r.test_type == tt]
                if not tt_results:
                    continue
                tt_hits = sum(1 for r in tt_results if r.hit)
                tt_precision = sum(r.precision for r in tt_results) / len(tt_results)
                print(f"    {tt}: {tt_hits}/{len(tt_results)} hits, precision: {tt_precision:.1%}")

        # Detailed table
        print("\n" + "-" * 100)
        print(f"  {'Test':<45} | {'Approach':<15} | {'Query':<30} | {'Prec':>5} | {'Hit'}")
        print(f"  {'-'*45}-+-{'-'*15}-+-{'-'*30}-+-{'-'*5}-+-{'-'*3}")
        for tc in TEST_CASES:
            for approach in approaches:
                r = next((r for r in self.results
                          if r.test_name == tc["name"] and r.approach == approach), None)
                if r:
                    name_short = r.test_name[:43] + ".." if len(r.test_name) > 45 else r.test_name
                    query_short = r.search_query[:28] + ".." if len(r.search_query) > 30 else r.search_query
                    mark = "✓" if r.hit else "✗"
                    print(f"  {name_short:<45} | {approach:<15} | {query_short:<30} | "
                          f"{r.precision:>4.0%} | {mark}")
            print()

        print("=" * 100)


class RegexIntentProvider:
    """Same as in the scaling benchmark."""

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
        "actually", "wait", "first", "show", "looking", "any",
    ])

    def __init__(self, max_keywords: int = 5):
        self._max_keywords = max_keywords

    def derive_intent(self, messages: list[dict], model=None) -> str:
        if not messages:
            return ""
        # Get last user message
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
        words = re.findall(r'\b[a-zA-Z]{3,}\b', last_msg.lower())
        keywords = [w for w in words if w not in self._STOP_WORDS]
        return " ".join(keywords[:self._max_keywords])


def _generate_tool_definitions(count: int) -> list[dict]:
    """Same synthetic tools as other benchmarks."""
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
        properties = {p: {"type": "string", "description": f"The {p} parameter"} for p in params}
        tools.append({
            "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties, "required": params},
        })
    return tools


def _get_lambda_zip() -> bytes:
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
                  "serverInfo": {"name": "relevance-bench", "version": "1.0.0"}}
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


def setup_infra(session, region: str) -> tuple[str, str]:
    iam = session.client("iam")
    lambda_client = session.client("lambda", region_name=region)
    try:
        role_arn = iam.get_role(RoleName=_ROLE_NAME)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(RoleName=_ROLE_NAME, AssumeRolePolicyDocument=json.dumps(TRUST_POLICY),
                               Description="Relevance benchmark role")
        role_arn = resp["Role"]["Arn"]
        iam.attach_role_policy(RoleName=_ROLE_NAME,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
        time.sleep(10)
    try:
        lambda_arn = lambda_client.get_function(FunctionName=_LAMBDA_NAME)["Configuration"]["FunctionArn"]
        lambda_client.update_function_code(FunctionName=_LAMBDA_NAME, ZipFile=_get_lambda_zip())
    except lambda_client.exceptions.ResourceNotFoundException:
        resp = lambda_client.create_function(
            FunctionName=_LAMBDA_NAME, Runtime="python3.10", Role=role_arn,
            Handler="lambda_function.lambda_handler", Code={"ZipFile": _get_lambda_zip()},
            Timeout=30)
        lambda_arn = resp["FunctionArn"]
        waiter = lambda_client.get_waiter("function_active_v2")
        waiter.wait(FunctionName=_LAMBDA_NAME)
    policy = {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": lambda_arn}]}
    iam.put_role_policy(RoleName=_ROLE_NAME, PolicyName="lambda-invoke", PolicyDocument=json.dumps(policy))
    return role_arn, lambda_arn


def teardown_infra(session, region: str):
    iam = session.client("iam")
    lambda_client = session.client("lambda", region_name=region)
    try:
        lambda_client.delete_function(FunctionName=_LAMBDA_NAME)
    except Exception:
        pass
    try:
        iam.delete_role_policy(RoleName=_ROLE_NAME, PolicyName="lambda-invoke")
    except Exception:
        pass
    try:
        iam.detach_role_policy(RoleName=_ROLE_NAME,
                               PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
    except Exception:
        pass
    try:
        iam.delete_role(RoleName=_ROLE_NAME)
    except Exception:
        pass


def _search_gateway(mcp_client, query: str) -> list[str]:
    """Call the gateway's semantic search and return tool names."""
    result = mcp_client.call_tool_sync(
        tool_use_id="relevance-search",
        name="x_amz_bedrock_agentcore_search",
        arguments={"query": query},
    )
    tool_names = []
    if not result or not isinstance(result, dict):
        return tool_names

    tool_defs = []
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and "tools" in structured:
        tool_defs = structured["tools"]
    else:
        for block in result.get("content", []):
            if isinstance(block, dict) and "text" in block:
                try:
                    data = json.loads(block["text"])
                    if isinstance(data, dict) and "tools" in data:
                        tool_defs = data["tools"]
                        break
                except (json.JSONDecodeError, TypeError):
                    continue

    for td in tool_defs:
        if isinstance(td, dict) and "name" in td:
            tool_names.append(td["name"])
    return tool_names


def _get_raw_message(messages: list[dict]) -> str:
    """Get the last user message text as-is."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                return " ".join(
                    b.get("text", "") for b in content if isinstance(b, dict) and "text" in b
                )
    return ""


def _check_relevance(tool_names: list[str], expected_categories: list[str]) -> tuple[int, bool]:
    """Count matching tools and check if at least one expected category is present."""
    matching = 0
    hit = False
    for name in tool_names:
        name_lower = name.lower()
        for cat in expected_categories:
            if cat in name_lower:
                matching += 1
                hit = True
                break
    return matching, hit


def run_relevance_tests(mcp_client, region: str, suite: RelevanceSuite):
    """Run all test cases for all three approaches."""
    from bedrock_agentcore.gateway.integrations.strands.plugins.agentcore_tool_search.intent_providers import (
        StrandsIntentProvider,
    )

    llm_provider = StrandsIntentProvider(message_window=5)
    regex_provider = RegexIntentProvider(max_keywords=5)

    for tc in TEST_CASES:
        messages = tc["messages"]
        expected = tc["expected_categories"]
        logger.info("Testing: %s", tc["name"])

        # 1. Raw message approach
        raw_query = _get_raw_message(messages)
        logger.info("  [raw] query: %s", raw_query[:60])
        raw_tools = _search_gateway(mcp_client, raw_query)
        raw_matching, raw_hit = _check_relevance(raw_tools, expected)
        suite.add(RelevanceResult(
            test_name=tc["name"], test_type=tc["type"], approach="raw_message",
            search_query=raw_query, expected_categories=expected,
            tools_returned=raw_tools, matching_tools=raw_matching,
            total_tools_returned=len(raw_tools),
            precision=raw_matching / len(raw_tools) if raw_tools else 0,
            hit=raw_hit, messages=messages,
        ))

        # 2. LLM intent approach
        llm_intent = llm_provider.derive_intent(messages)
        logger.info("  [llm] intent: %s", llm_intent[:60])
        llm_tools = _search_gateway(mcp_client, llm_intent) if llm_intent else []
        llm_matching, llm_hit = _check_relevance(llm_tools, expected)
        suite.add(RelevanceResult(
            test_name=tc["name"], test_type=tc["type"], approach="llm_intent",
            search_query=llm_intent, expected_categories=expected,
            tools_returned=llm_tools, matching_tools=llm_matching,
            total_tools_returned=len(llm_tools),
            precision=llm_matching / len(llm_tools) if llm_tools else 0,
            hit=llm_hit, messages=messages,
        ))

        # 3. Regex intent approach
        regex_intent = regex_provider.derive_intent(messages)
        logger.info("  [regex] intent: %s", regex_intent[:60])
        regex_tools = _search_gateway(mcp_client, regex_intent) if regex_intent else []
        regex_matching, regex_hit = _check_relevance(regex_tools, expected)
        suite.add(RelevanceResult(
            test_name=tc["name"], test_type=tc["type"], approach="regex_intent",
            search_query=regex_intent, expected_categories=expected,
            tools_returned=regex_tools, matching_tools=regex_matching,
            total_tools_returned=len(regex_tools),
            precision=regex_matching / len(regex_tools) if regex_tools else 0,
            hit=regex_hit, messages=messages,
        ))


def main():
    parser = argparse.ArgumentParser(description="Benchmark: Intent Provider Search Relevance")
    parser.add_argument("--region", default="us-east-1", help="AWS region")
    parser.add_argument("--profile", default=None, help="AWS profile")
    parser.add_argument("--tool-count", type=int, default=100, help="Number of tools to register")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile

    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
    from strands.tools.mcp import MCPClient

    gw_client = GatewayClient(region_name=args.region)
    role_arn, lambda_arn = setup_infra(session, args.region)

    tools = _generate_tool_definitions(args.tool_count)
    prefix = f"{_BENCHMARK_PREFIX}-{args.tool_count}t-{int(time.time())}"

    gw = gw_client.create_gateway_and_wait(
        name=f"{prefix}-gw", roleArn=role_arn, authorizerType="NONE",
        protocolType="MCP", protocolConfiguration={"mcp": {"searchType": "SEMANTIC"}},
    )
    gateway_id = gw["gatewayId"]
    logger.info("Created gateway: %s", gateway_id)

    target = gw_client.create_gateway_target_and_wait(
        gatewayIdentifier=gateway_id, name=f"{prefix}-target",
        targetConfiguration={"mcp": {"lambda": {"lambdaArn": lambda_arn,
                             "toolSchema": {"inlinePayload": tools}}}},
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    target_id = target["targetId"]
    logger.info("Created target: %s", target_id)
    logger.info("Waiting 60s for semantic indexing...")
    time.sleep(60)

    endpoint = f"https://{gateway_id}.gateway.bedrock-agentcore.{args.region}.amazonaws.com/mcp"
    mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
        endpoint=endpoint, aws_region=args.region, aws_service="bedrock-agentcore",
    ))

    suite = RelevanceSuite()

    try:
        with mcp_client:
            run_relevance_tests(mcp_client, args.region, suite)
    finally:
        try:
            gw_client.delete_gateway_target_and_wait(gatewayIdentifier=gateway_id, targetId=target_id)
        except Exception:
            pass
        try:
            gw_client.delete_gateway_and_wait(gatewayIdentifier=gateway_id)
        except Exception:
            pass
        teardown_infra(session, args.region)

    suite.print_report()

    output_path = os.path.join(os.path.dirname(__file__), "results", "intent_relevance_results.json")
    with open(output_path, "w") as f:
        json.dump([{
            "test_name": r.test_name, "test_type": r.test_type, "approach": r.approach,
            "messages": r.messages,
            "search_query": r.search_query, "expected_categories": r.expected_categories,
            "tools_returned": r.tools_returned, "matching_tools": r.matching_tools,
            "total_tools_returned": r.total_tools_returned,
            "precision": r.precision, "hit": r.hit,
        } for r in suite.results], f, indent=2)
    logger.info("Results saved to %s", output_path)


if __name__ == "__main__":
    main()
