#!/usr/bin/env python3
"""
Response Quality Evaluator using Bedrock LLM
"""

import boto3
import json
import pandas as pd
from typing import Dict


class ResponseQualityEvaluator:
    def __init__(self, region_name="us-east-1"):
        self.bedrock = boto3.client("bedrock-runtime", region_name=region_name)
        self.model_id = "anthropic.claude-3-sonnet-20240229-v1:0"

    def evaluate_response(self, query: str, response: str, context: str = "") -> Dict[str, float]:
        """Evaluate response quality on faithfulness, correctness, and helpfulness"""

        prompt = f"""You are an expert evaluator. Rate the following AI assistant response on three dimensions:

QUERY: {query}

CONTEXT (if available): {context}

RESPONSE: {response}

Rate each dimension from 0.0 to 1.0:

1. FAITHFULNESS (0.0-1.0): How well does the response stick to the provided context without hallucination?
2. CORRECTNESS (0.0-1.0): How factually accurate and technically correct is the response?
3. HELPFULNESS (0.0-1.0): How useful and relevant is the response to answering the user's query?

Respond ONLY with a JSON object in this exact format:
{{"faithfulness": 0.8, "correctness": 0.9, "helpfulness": 0.7}}"""

        try:
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            }

            response_bedrock = self.bedrock.invoke_model(modelId=self.model_id, body=json.dumps(body))

            result = json.loads(response_bedrock["body"].read())
            content = result["content"][0]["text"]

            # Parse JSON response
            scores = json.loads(content.strip())
            return {
                "faithfulness": float(scores.get("faithfulness", 0.0)),
                "correctness": float(scores.get("correctness", 0.0)),
                "helpfulness": float(scores.get("helpfulness", 0.0)),
            }

        except Exception as e:
            print(f"Error evaluating response: {e}")
            return {"faithfulness": 0.0, "correctness": 0.0, "helpfulness": 0.0}


def evaluate_responses_from_csv(csv_path: str, output_path: str = None):
    """Evaluate responses from trace metrics CSV"""

    evaluator = ResponseQualityEvaluator()
    df = pd.read_csv(csv_path)

    results = []

    for idx, row in df.iterrows():
        if pd.isna(row.get("user_query")) or pd.isna(row.get("final_response")):
            continue

        print(f"Evaluating response {idx + 1}/{len(df)}")

        # Extract context from tool calls (use first 500 chars of response as context)
        context = (
            str(row["final_response"])[:500] + "..."
            if len(str(row["final_response"])) > 500
            else str(row["final_response"])
        )

        scores = evaluator.evaluate_response(query=row["user_query"], response=row["final_response"], context=context)

        result = {
            "trace_id": row.get("trace_id", ""),
            "user_query": row["user_query"],
            "final_response": row["final_response"],
            "context_used": context,
            **scores,
        }
        results.append(result)

    # Save results
    results_df = pd.DataFrame(results)
    output_file = output_path or "response_quality_scores.csv"
    results_df.to_csv(output_file, index=False)
    print(f"✓ Saved {output_file} ({len(results_df)} rows)")

    # Print summary
    print("\nResponse Quality Summary:")
    print(f"- Avg Faithfulness: {results_df['faithfulness'].mean():.3f}")
    print(f"- Avg Correctness: {results_df['correctness'].mean():.3f}")
    print(f"- Avg Helpfulness: {results_df['helpfulness'].mean():.3f}")

    return results_df


if __name__ == "__main__":
    # Evaluate responses from trace metrics
    evaluate_responses_from_csv("trace_metrics.csv")
