"""One bounded Langfuse experiment on an existing, IAM-authenticated DEV/TST Runtime."""

import argparse
import json
import os
import re
import uuid
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from langfuse import Evaluation, Langfuse

DATA = Path(__file__).with_name("dataset.json")
MAX_BYTES = 256 * 1024


def runtime_region(arn, account):
    match = re.fullmatch(
        r"arn:aws:bedrock-agentcore:([a-z0-9-]+):([0-9]{12}):runtime/[A-Za-z][A-Za-z0-9_]{0,47}-[A-Za-z0-9]{10}", arn
    )
    if not match or match[2] != account:
        raise ValueError("Expected a Runtime ARN in the approved account")
    return match[1]


def destination(url):
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Explicitly select your trusted Langfuse HTTPS origin")
    return url.rstrip("/")


def invoke(runtime, arn, question, trace_id, parent_id):
    if not re.fullmatch(r"[0-9a-f]{32}", trace_id or "") or not re.fullmatch(r"[0-9a-f]{16}", parent_id or ""):
        raise ValueError("No active experiment trace and parent observation")
    result = runtime.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=str(uuid.uuid4()),
        contentType="application/json",
        accept="application/json",
        payload=json.dumps({"prompt": question, "trace_id": trace_id, "parent_obs_id": parent_id}).encode(),
    )
    body = result["response"]
    try:
        if result.get("contentType", "").split(";")[0] != "application/json":
            raise ValueError("Expected the sample's JSON string response")
        raw = body.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("Runtime response too large")
        answer = json.loads(raw)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Expected a nonempty text answer")
        return answer
    finally:
        body.close()


def coverage(*, output, expected_output, **kwargs):
    # An intentionally transparent string-coverage measure, not an LLM judge or a truth score.
    facts = expected_output["response_facts"]
    normalized = " ".join(output.casefold().split())
    return Evaluation(
        name="reference_phrase_coverage",
        value=sum(" ".join(f.casefold().split()) in normalized for f in facts) / len(facts),
    )


def experiment(langfuse, runtime, arn, items):
    if not 1 <= len(items) <= 4:
        raise ValueError("Select between one and four fixture questions")
    for item in items:
        facts = item["expected_output"]["response_facts"]
        if (
            not isinstance(item["input"]["question"], str)
            or not item["input"]["question"].strip()
            or not isinstance(facts, list)
            or not facts
            or not all(isinstance(f, str) and f.strip() for f in facts)
        ):
            raise ValueError("Each item needs a question and reviewed nonempty reference phrases")

    def task(*, item, **kwargs):
        return invoke(
            runtime,
            arn,
            item["input"]["question"],
            langfuse.get_current_trace_id(),
            langfuse.get_current_observation_id(),
        )

    result = langfuse.run_experiment(
        name="agentcore-docs-evaluation",
        run_name=f"sandbox-{uuid.uuid4().hex}",
        data=items,
        task=task,
        evaluators=[coverage],
        max_concurrency=1,
    )
    if len(result.item_results) != len(items):
        raise RuntimeError("Incomplete experiment; inspect its traces")
    scores = []
    for entry in result.item_results:
        if not isinstance(entry.output, str) or not entry.output.strip():
            raise RuntimeError("Missing answer; do not count the experiment as a pass")
        values = [e.value for e in entry.evaluations if e.name == "reference_phrase_coverage"]
        if len(values) != 1 or type(values[0]) not in (int, float) or not 0 <= values[0] <= 1:
            raise RuntimeError("Missing/invalid score; inspect experiment diagnostics")
        scores.append(values[0])
    return scores


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--runtime-arn", required=True)
    cli.add_argument("--account", required=True)
    cli.add_argument("--langfuse-origin", required=True)
    cli.add_argument("--limit", type=int, choices=range(1, 5), default=1)
    args = cli.parse_args()
    origin = destination(args.langfuse_origin)
    region = runtime_region(args.runtime_arn, args.account)
    aws = boto3.Session(region_name=region)
    if aws.client("sts").get_caller_identity()["Account"] != args.account:
        cli.error("Wrong AWS account")
    # No control-plane client, CRUD, deployment, or runtime discovery by name.
    with closing(
        aws.client(
            "bedrock-agentcore", config=Config(connect_timeout=5, read_timeout=120, retries={"total_max_attempts": 1})
        )
    ) as runtime:
        client = Langfuse(
            base_url=origin, public_key=os.environ["LANGFUSE_PUBLIC_KEY"], secret_key=os.environ["LANGFUSE_SECRET_KEY"]
        )
        try:
            scores = experiment(client, runtime, args.runtime_arn, json.loads(DATA.read_text())[: args.limit])
            print(f"Completed {len(scores)} item(s); mean reference phrase coverage: {sum(scores) / len(scores):.2f}")
        finally:
            client.shutdown()


if __name__ == "__main__":
    main()
