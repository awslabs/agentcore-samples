"""
Invoke the Lambda agent 5 times with diverse prompts to generate evaluation sessions.

Records invocation timestamps and Lambda execution metadata in lambda_config.json
for use by evaluate.py.

Usage:
    python invoke.py
"""

import json
import time

import boto3

PROMPTS = [
    "What is the capital of France and what is it famous for?",
    "Explain how photosynthesis works in simple terms.",
    "What are the main differences between Python and JavaScript?",
    "How do black holes form and what happens at the event horizon?",
    "Give me 3 practical tips for improving sleep quality.",
]

# ── Load config ────────────────────────────────────────────────────────────────

with open("lambda_config.json") as f:
    config = json.load(f)

FUNCTION_NAME = config["function_name"]
REGION = config["region"]

lam = boto3.client("lambda", region_name=REGION)


def invoke(prompt: str, idx: int) -> dict:
    payload = json.dumps({"prompt": prompt}).encode()
    start = time.time()
    resp = lam.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=payload,
    )
    elapsed = time.time() - start

    body = json.loads(resp["Payload"].read())
    status = resp["StatusCode"]
    error = resp.get("FunctionError")

    print(f"\n[{idx + 1}/5] Prompt: {prompt[:60]}...")
    if error:
        print(f"  ERROR: {error}")
        print(f"  Body:  {body}")
    else:
        result_text = body.get("result", "")
        print(f"  Response ({elapsed:.1f}s): {str(result_text)[:120]}...")

    return {
        "prompt": prompt,
        "status_code": status,
        "error": error,
        "elapsed_s": round(elapsed, 2),
    }


def main():
    print("=" * 60)
    print(f"Invoking {FUNCTION_NAME} with {len(PROMPTS)} prompts")
    print("=" * 60)

    start_time = int(time.time())
    results = []

    for i, prompt in enumerate(PROMPTS):
        result = invoke(prompt, i)
        results.append(result)
        if i < len(PROMPTS) - 1:
            time.sleep(2)  # small gap between invocations

    end_time = int(time.time()) + 5  # +5s buffer

    # Save invocation window to config for evaluate.py
    config["eval_start_time"] = start_time - 30  # 30s before first invoke
    config["eval_end_time"] = end_time + 120  # 2 min after last invoke (span flush time)
    config["invocation_results"] = results

    with open("lambda_config.json", "w") as f:
        json.dump(config, f, indent=2)

    successes = sum(1 for r in results if not r["error"])
    print(f"\n{'=' * 60}")
    print(f"Completed: {successes}/{len(PROMPTS)} successful invocations")
    print(f"Time window saved: {start_time - 30} → {end_time + 120}")
    print("\nNext: python evaluate.py  (wait ~2 min for spans to appear in CloudWatch)")
    print("=" * 60)


if __name__ == "__main__":
    main()
