"""Generate draft Cedar statements; never install a policy or change a Gateway."""

import argparse
import json
import re
import time
import uuid
from pathlib import Path

import boto3


def verified_engine(client, account, region, gateway_id, engine_id):
    base = f"arn:aws:bedrock-agentcore:{region}:{account}:"
    gateway = client.get_gateway(gatewayIdentifier=gateway_id)
    engine = client.get_policy_engine(policyEngineId=engine_id)
    if (
        gateway["gatewayArn"] != base + "gateway/" + gateway_id
        or engine["policyEngineArn"] != base + "policy-engine/" + engine_id
        or gateway.get("policyEngineConfiguration", {}).get("arn") != engine["policyEngineArn"]
        or gateway["status"] != "READY"
        or engine["status"] != "ACTIVE"
    ):
        raise ValueError("Use a READY Gateway with the specified ACTIVE engine attached in the selected account/Region")
    return gateway["gatewayArn"]


def start(client, engine_id, gateway_arn, intent):
    if not 1 <= len(intent.strip()) <= 2000:
        raise ValueError("Supply 1 to 2000 characters of non-sensitive policy intent")
    token = uuid.uuid4().hex
    return client.start_policy_generation(
        policyEngineId=engine_id,
        resource={"arn": gateway_arn},
        content={"rawText": intent.strip()},
        name="preview_" + token,
        clientToken=str(uuid.UUID(token)),
    )["policyGenerationId"]


def collect(client, engine_id, gateway_arn, generation_id):
    params = {"policyEngineId": engine_id, "policyGenerationId": generation_id}
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        job = client.get_policy_generation(**params)
        if job["resource"].get("arn") != gateway_arn or job["policyEngineId"] != engine_id:
            raise ValueError("Generation belongs to a different resource")
        if job["status"] == "GENERATED":
            break
        if job["status"] != "GENERATING":
            raise RuntimeError(f"Generation did not succeed: {job['status']}")
        time.sleep(3)
    else:
        raise TimeoutError("Generation still pending; resume with its ID")
    assets = []
    while True:
        page = client.list_policy_generation_assets(**params)
        for asset in page.get("policyGenerationAssets", []):
            definition = asset.get("definition", {})
            policy = definition.get("policy") or definition.get("cedar") or {}
            assets.append(
                {
                    "id": asset["policyGenerationAssetId"],
                    "statement": policy.get("statement"),
                    "findings": asset["findings"],
                }
            )
        if not page.get("nextToken"):
            return {"generationId": generation_id, "findings": job.get("findings"), "drafts": assets}
        params["nextToken"] = page["nextToken"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("account", "region", "gateway-id", "engine-id"):
        parser.add_argument("--" + name, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--intent-file", type=Path)
    source.add_argument("--generation-id", help="Resume without starting a new generation")
    args = parser.parse_args()
    session = boto3.Session(region_name=args.region)
    if (
        not re.fullmatch(r"[0-9]{12}", args.account)
        or session.client("sts").get_caller_identity()["Account"] != args.account
    ):
        parser.error("Account does not match caller")
    client = session.client("bedrock-agentcore-control")
    arn = verified_engine(client, args.account, args.region, args.gateway_id, args.engine_id)
    generation = args.generation_id or start(client, args.engine_id, arn, args.intent_file.read_text(encoding="utf-8"))
    print(f"Generation ID: {generation}", flush=True)
    print(json.dumps(collect(client, args.engine_id, arn, generation), indent=2))


if __name__ == "__main__":
    main()
