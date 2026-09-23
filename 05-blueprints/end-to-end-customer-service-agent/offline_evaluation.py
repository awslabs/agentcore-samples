#!/usr/bin/env python3
"""
Simplified evaluation script for agent performance
"""

import os
import time
import json
import logging
import requests
import pandas as pd
from datetime import datetime
from langfuse import get_client

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def load_groundtruth(file_path="groundtruth.json"):
    """Load test queries and expected tools"""
    with open(file_path, "r") as f:
        return json.load(f)


def run_tests(groundtruth, agent_url=None):
    """Run test queries and collect trace IDs"""
    if not agent_url:
        agent_url = os.getenv("AGENT_ARN", "http://localhost:8080")

    today = datetime.now().strftime("%Y-%m-%d")
    eval_tag = f"{today}-offline_evaluation"

    results = []
    for i, gt in enumerate(groundtruth, 1):
        print(f"{i}/{len(groundtruth)}: {gt['query'][:50]}...")

        try:
            response = requests.post(
                f"{agent_url}/invocations",
                json={"input": {"prompt": gt["query"], "langfuse_tags": [eval_tag]}},
                timeout=100,
            )

            response_data = response.json() if response.status_code == 200 else None
            print(response_data)

            # Extract trace_id from nested structure
            trace_id = None
            if response_data:
                print(f"  Response data structure: {type(response_data)}")
                if "output" in response_data and isinstance(response_data["output"], dict):
                    trace_id = response_data["output"].get("trace_id")
                    print(f"  Found trace_id in output: {trace_id}")
                elif "trace_id" in response_data:
                    trace_id = response_data["trace_id"]
                    print(f"  Found trace_id at root: {trace_id}")
                else:
                    print("  No trace_id found in response structure")

            # Debug logging
            print(f"  Response status: {response.status_code}")
            print(f"  Response data keys: {list(response_data.keys()) if response_data else 'None'}")
            if response_data and "output" in response_data:
                print(f"  Output keys: {list(response_data['output'].keys())}")
                print(f"  Output structure: {json.dumps(response_data['output'], indent=2)[:500]}...")
            print(f"  Extracted trace ID: {trace_id}")

            # Extract additional metrics from response
            model_used = None
            timestamp = None
            tools_used = None
            citations = None

            if response_data and "output" in response_data:
                output = response_data["output"]
                model_used = output.get("model")
                timestamp = output.get("timestamp")
                print(f"  Extracted model: {model_used}, timestamp: {timestamp}")

                if "metadata" in output:
                    metadata = output["metadata"]
                    tools_used = metadata.get("tools_used")
                    citations = metadata.get("citations")
                    print(f"  Extracted tools_used: {tools_used}, citations: {type(citations)}")

            results.append(
                {
                    "query": gt["query"],
                    "expected_tools": gt["expected_tools"],
                    "success": response.status_code == 200,
                    "trace_id": trace_id,
                    "model_used": model_used,
                    "timestamp": timestamp,
                    "tools_used": tools_used,
                    "citations": citations,
                }
            )
        except Exception as e:
            print(f"  Error: {e}")
            results.append(
                {
                    "query": gt["query"],
                    "expected_tools": gt["expected_tools"],
                    "success": False,
                    "trace_id": None,
                    "model_used": None,
                    "timestamp": None,
                    "tools_used": None,
                    "citations": None,
                    "error": str(e),
                }
            )

        time.sleep(2)

    return results


def extract_metrics(langfuse, trace_ids):
    """Extract tool calls and scores from traces"""
    metrics = []
    print(f"\nExtracting metrics from {len(trace_ids)} trace IDs...")

    for i, trace_id in enumerate(trace_ids):
        print(f"  Processing trace {i + 1}/{len(trace_ids)}: {trace_id}")
        try:
            trace = langfuse.api.trace.get(trace_id)
            print(f"    ✓ Found trace: {trace.id if hasattr(trace, 'id') else 'unknown'}")
            observations = langfuse.api.observations.get_many(trace_id=trace_id)
            print(f"    ✓ Found {len(observations.data)} observations")

            for obs in observations.data:
                if obs.type == "CHAIN" and obs.name == "LangGraph" and obs.output:
                    messages = obs.output.get("messages", [])

                    # Extract basic info
                    user_query = next((m["content"] for m in messages if m["type"] == "human"), "")
                    final_response = next(
                        (m["content"] for m in messages if m["type"] == "ai" and not m.get("tool_calls")),
                        "",
                    )

                    # Extract tool calls
                    tool_calls = []
                    for msg in messages:
                        if msg["type"] == "ai" and msg.get("tool_calls"):
                            tool_calls.extend([tc["name"] for tc in msg["tool_calls"]])

                    # Extract retrieval scores from citations in metadata or tool content
                    retrieval_scores = []

                    # First try to get from final AI message metadata (where citations are stored)
                    for msg in messages:
                        if msg["type"] == "ai" and not msg.get("tool_calls") and "metadata" in msg:
                            metadata = msg.get("metadata", {})
                            if "citations" in metadata:
                                try:
                                    import html

                                    citations_str = metadata["citations"]
                                    if isinstance(citations_str, str):
                                        citations = json.loads(html.unescape(citations_str))
                                    else:
                                        citations = citations_str
                                    retrieval_scores = [
                                        c.get("relevance_score", 0) for c in citations if isinstance(c, dict)
                                    ]
                                except (
                                    json.JSONDecodeError,
                                    ValueError,
                                    TypeError,
                                ) as e:
                                    # Skip malformed citation data in metadata
                                    logging.warning(f"Skipping malformed citation data in metadata: {e}")

                    # Fallback: try to get from tool message content
                    if not retrieval_scores:
                        for msg in messages:
                            if msg["type"] == "tool" and msg["name"] == "retrieve_context":
                                try:
                                    content = (
                                        json.loads(msg["content"])
                                        if isinstance(msg["content"], str)
                                        else msg["content"]
                                    )
                                    if isinstance(content, dict) and "citations" in content:
                                        citations = content["citations"]
                                        retrieval_scores = [
                                            c.get("relevance_score", 0) for c in citations if isinstance(c, dict)
                                        ]
                                except (
                                    json.JSONDecodeError,
                                    ValueError,
                                    TypeError,
                                ) as e:
                                    # Skip malformed tool content data
                                    logging.warning(f"Skipping malformed tool content data: {e}")

                    # Extract tool latencies
                    tool_latencies = {}
                    for obs_row in observations.data:
                        if obs_row.type == "TOOL" and obs_row.latency:
                            tool_latencies[obs_row.name] = obs_row.latency

                    metrics.append(
                        {
                            "trace_id": trace_id,
                            "user_query": user_query,
                            "final_response": final_response,
                            "tool_calls": tool_calls,
                            "retrieval_scores": retrieval_scores,
                            "trace_success": (trace.level != "ERROR" if hasattr(trace, "level") else True),
                            "total_latency": obs.latency if obs.latency else 0,
                            "tool_latencies": tool_latencies,
                        }
                    )
        except Exception as e:
            print(f"    ✗ Error processing trace {trace_id}: {e}")
            continue

    return pd.DataFrame(metrics)


def evaluate_tools(metrics_df, test_results):
    """Calculate tool selection accuracy"""
    results = []

    for test in test_results:
        query = test["query"]
        expected_tools = set(test["expected_tools"])

        # Get retrieval scores from test results citations if available
        retrieval_scores = []
        if test.get("citations"):
            try:
                import html

                citations_str = test["citations"]
                if isinstance(citations_str, str):
                    citations = json.loads(html.unescape(citations_str))
                else:
                    citations = citations_str
                retrieval_scores = [c.get("relevance_score", 0) for c in citations if isinstance(c, dict)]
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                # Skip malformed citations in test results
                logging.warning(f"Skipping malformed citations in test results: {e}")

        # Extract tools from tools_used metadata
        actual_tools = set()
        if test.get("tools_used"):
            # Parse tools_used string (e.g., "retrieve_context")
            tools_str = test["tools_used"]
            if isinstance(tools_str, str):
                actual_tools = {tools_str} if tools_str else set()

        # Find matching trace for additional data
        if not metrics_df.empty and "user_query" in metrics_df.columns:
            match = metrics_df[metrics_df["user_query"].str.contains(query[:20], na=False)]
            if not match.empty:
                trace_tools = set(match.iloc[0]["tool_calls"])
                actual_tools = actual_tools.union(trace_tools)
                if not retrieval_scores and match.iloc[0]["retrieval_scores"]:
                    retrieval_scores = match.iloc[0]["retrieval_scores"]

        accuracy = 1.0 if expected_tools == actual_tools else 0.0
        max_retrieval_score = max(retrieval_scores) if retrieval_scores else None

        results.append(
            {
                "query": query,
                "expected_tools": list(expected_tools),
                "actual_tools": list(actual_tools),
                "tool_accuracy": accuracy,
                "retrieval_score": max_retrieval_score,
                "all_retrieval_scores": retrieval_scores,
            }
        )

    return pd.DataFrame(results)


def evaluate_response_quality(metrics_df):
    """Evaluate response quality using Bedrock LLM"""
    try:
        from response_quality_evaluator import ResponseQualityEvaluator

        evaluator = ResponseQualityEvaluator()

        quality_scores = []
        for _, row in metrics_df.iterrows():
            if pd.notna(row["user_query"]) and pd.notna(row["final_response"]):
                scores = evaluator.evaluate_response(
                    query=row["user_query"],
                    response=row["final_response"],
                    context=row["final_response"][:500],
                )
                quality_scores.append(scores)

        if quality_scores:
            return {
                "faithfulness": sum(s["faithfulness"] for s in quality_scores) / len(quality_scores),
                "correctness": sum(s["correctness"] for s in quality_scores) / len(quality_scores),
                "helpfulness": sum(s["helpfulness"] for s in quality_scores) / len(quality_scores),
            }
    except Exception as e:
        print(f"Response quality evaluation failed: {e}")

    return {"faithfulness": 0, "correctness": 0, "helpfulness": 0}


def calculate_metrics(metrics_df, evaluation_df, quality_scores=None):
    """Calculate performance metrics"""
    if metrics_df.empty:
        return {}

    # Success rate
    success_rate = (metrics_df["trace_success"].sum() / len(metrics_df)) * 100

    # Tool accuracy
    tool_accuracy = evaluation_df["tool_accuracy"].mean() if not evaluation_df.empty else 0

    # Retrieval quality
    retrieval_scores = [
        score
        for scores in evaluation_df["retrieval_score"].dropna()
        for score in (scores if isinstance(scores, list) else [scores])
    ]
    retrieval_quality = (
        (sum(1 for s in retrieval_scores if s >= 0.35) / len(retrieval_scores)) * 100 if retrieval_scores else 0
    )

    result = {
        "success_rate": success_rate,
        "tool_accuracy": tool_accuracy,
        "retrieval_quality": retrieval_quality,
        "total_traces": len(metrics_df),
    }

    # Add response quality if available
    if quality_scores:
        result.update(quality_scores)

    return result


def main():
    print("Starting evaluation...")
    print("DEBUG: Evaluation script is running with trace ID extraction enabled")

    # Setup
    try:
        langfuse = get_client()
        print("✓ Langfuse connected")
    except Exception as e:
        print(f"✗ Langfuse failed: {e}")
        return

    # Load and run tests
    groundtruth = load_groundtruth()
    test_results = run_tests(groundtruth)

    # Wait and extract metrics
    print(f"Waiting {len(groundtruth) * 15} seconds for traces...")
    time.sleep(len(groundtruth) * 15)

    trace_ids = [r["trace_id"] for r in test_results if r["trace_id"]]
    print("\nTrace ID Summary:")
    print(f"- Total requests: {len(test_results)}")
    print(f"- Successful requests: {sum(1 for r in test_results if r['success'])}")
    print(f"- Requests with trace IDs: {len(trace_ids)}")
    print(f"- Trace IDs: {trace_ids}")

    # Debug: Print test results details
    print("\nTest Results Debug:")
    for i, result in enumerate(test_results):
        print(
            f"  {i + 1}. Success: {result['success']}, Trace ID: {result.get('trace_id')}, Error: {result.get('error', 'None')}"
        )

    if not trace_ids:
        print("\nNo trace IDs found. Checking if we can extract metrics from test_results directly...")
        # Try to create basic metrics from test_results
        basic_metrics = []
        for test in test_results:
            if test["success"]:
                basic_metrics.append(
                    {
                        "trace_id": test.get("trace_id", "unknown"),
                        "user_query": test["query"],
                        "final_response": "",  # Will be empty without Langfuse data
                        "tool_calls": ([test.get("tools_used")] if test.get("tools_used") else []),
                        "retrieval_scores": [],
                        "trace_success": True,
                        "total_latency": 0,
                        "tool_latencies": {},
                    }
                )
        metrics_df = pd.DataFrame(basic_metrics)
        print(f"Created basic metrics for {len(basic_metrics)} successful requests")
    else:
        metrics_df = extract_metrics(langfuse, trace_ids)
        print(f"Extracted metrics from Langfuse for {len(metrics_df)} traces")
    evaluation_df = evaluate_tools(metrics_df, test_results)

    # Evaluate response quality
    print("Evaluating response quality...")
    quality_scores = evaluate_response_quality(metrics_df)

    # Calculate and display results
    results = calculate_metrics(metrics_df, evaluation_df, quality_scores)

    # Calculate latency metrics
    avg_total_latency = (
        metrics_df["total_latency"].mean() if not metrics_df.empty and "total_latency" in metrics_df.columns else 0
    )
    avg_tool_latency = 0
    if not metrics_df.empty and "tool_latencies" in metrics_df.columns:
        all_tool_latencies = []
        for _, row in metrics_df.iterrows():
            if isinstance(row["tool_latencies"], dict):
                all_tool_latencies.extend(row["tool_latencies"].values())
        avg_tool_latency = sum(all_tool_latencies) / len(all_tool_latencies) if all_tool_latencies else 0

    print(f"\n{'=' * 40}")
    print("EVALUATION RESULTS")
    print(f"{'=' * 40}")
    print(f"Success Rate: {results.get('success_rate', 0):.1f}%")
    print(f"Tool Accuracy: {results.get('tool_accuracy', 0):.3f}")
    print(f"Retrieval Quality: {results.get('retrieval_quality', 0):.1f}%")
    print(f"Avg Total Latency: {avg_total_latency:.0f}ms")
    print(f"Avg Tool Latency: {avg_tool_latency:.0f}ms")
    print(f"Faithfulness: {results.get('faithfulness', 0):.3f}")
    print(f"Correctness: {results.get('correctness', 0):.3f}")
    print(f"Helpfulness: {results.get('helpfulness', 0):.3f}")
    print(f"Total Traces: {results.get('total_traces', 0)}")

    # Create comprehensive results file
    comprehensive_results = []

    # Include results from test_results that have response data
    for test in test_results:
        if test["success"] and test["trace_id"]:
            # Find matching trace metrics
            trace_match = (
                metrics_df[metrics_df["trace_id"] == test["trace_id"]] if not metrics_df.empty else pd.DataFrame()
            )
            eval_match = (
                evaluation_df[evaluation_df["query"] == test["query"]] if not evaluation_df.empty else pd.DataFrame()
            )

            result = {
                "trace_id": test["trace_id"],
                "query": test["query"],
                "response": (trace_match.iloc[0]["final_response"] if not trace_match.empty else ""),
                "model_used": test.get("model_used"),
                "timestamp": test.get("timestamp"),
                "tools_used_metadata": test.get("tools_used"),
                "expected_tools": test["expected_tools"],
                "actual_tools": (eval_match.iloc[0]["actual_tools"] if not eval_match.empty else []),
                "tool_accuracy": (eval_match.iloc[0]["tool_accuracy"] if not eval_match.empty else 0),
                "retrieval_score": (eval_match.iloc[0]["retrieval_score"] if not eval_match.empty else None),
                "all_retrieval_scores": (eval_match.iloc[0]["all_retrieval_scores"] if not eval_match.empty else []),
                "trace_success": (trace_match.iloc[0]["trace_success"] if not trace_match.empty else True),
                "total_latency_ms": (trace_match.iloc[0]["total_latency"] if not trace_match.empty else 0),
                "retrieve_context_latency_ms": (
                    trace_match.iloc[0]["tool_latencies"].get("retrieve_context", 0)
                    if not trace_match.empty and isinstance(trace_match.iloc[0]["tool_latencies"], dict)
                    else 0
                ),
                "web_search_latency_ms": (
                    trace_match.iloc[0]["tool_latencies"].get("web_search", 0)
                    if not trace_match.empty and isinstance(trace_match.iloc[0]["tool_latencies"], dict)
                    else 0
                ),
                "create_support_ticket_latency_ms": (
                    trace_match.iloc[0]["tool_latencies"].get("create_support_ticket", 0)
                    if not trace_match.empty and isinstance(trace_match.iloc[0]["tool_latencies"], dict)
                    else 0
                ),
            }

            # Add response quality if available
            if quality_scores and quality_scores.get("faithfulness", 0) > 0 and result["response"]:
                try:
                    from response_quality_evaluator import ResponseQualityEvaluator

                    evaluator = ResponseQualityEvaluator()
                    context = result["response"][:500] + "..." if len(result["response"]) > 500 else result["response"]
                    scores = evaluator.evaluate_response(
                        query=result["query"],
                        response=result["response"],
                        context=context,
                    )
                    result.update(scores)
                except Exception:
                    # Fallback to zero scores if response quality evaluation fails
                    result.update({"faithfulness": 0, "correctness": 0, "helpfulness": 0})

            comprehensive_results.append(result)

    # Save all results
    print("\nSaving results...")
    pd.DataFrame(comprehensive_results).to_csv("comprehensive_results.csv", index=False)
    print(f"✓ Saved comprehensive_results.csv ({len(comprehensive_results)} rows)")


if __name__ == "__main__":
    main()
