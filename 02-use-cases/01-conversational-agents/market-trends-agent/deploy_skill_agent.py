"""Deploy the native AgentSkills Market Trends runtime.

Usage:
    uv run python deploy_skill_agent.py --region us-west-2
"""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

import boto3  # type: ignore[import-untyped]
from boto3.session import Session  # type: ignore[import-untyped]

_ROOT = Path(__file__).resolve().parent
_CONFIG_PATH = _ROOT / "skill_agent_config.json"
_AGENT_FILE = _ROOT / "market_trends_skill_agent.py"
_SKILLS_DIR = _ROOT / "skills"
_SKILLS = (
    "earnings-snapshot",
    "portfolio-risk",
    "sector-rotation",
    "trend-analysis",
)
_PACKAGES = (
    "bedrock-agentcore==1.8.0",
    "strands-agents[otel]==1.38.0",
    "aws-opentelemetry-distro==0.19.0",
)
_MODEL_ID = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


def _validate_source() -> None:
    if not _AGENT_FILE.is_file():
        raise FileNotFoundError(_AGENT_FILE)
    discovered = tuple(sorted(path.parent.name for path in _SKILLS_DIR.glob("*/SKILL.md")))
    if discovered != _SKILLS:
        raise ValueError(f"Expected skills {_SKILLS}, found {discovered}")


def _install_command(target: Path) -> list[str]:
    uv = shutil.which("uv")
    if uv:
        return [
            uv,
            "pip",
            "install",
            "--target",
            str(target),
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.13",
            "--only-binary=:all:",
            "--no-compile",
            "--quiet",
            *_PACKAGES,
        ]
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(target),
        "--platform",
        "manylinux2014_aarch64",
        "--implementation",
        "cp",
        "--python-version",
        "3.13",
        "--only-binary=:all:",
        "--no-compile",
        "--quiet",
        *_PACKAGES,
    ]


def _build_artifact(work_dir: Path) -> Path:
    package_dir = work_dir / "package"
    package_dir.mkdir()
    subprocess.run(_install_command(package_dir), check=True)
    shutil.copy2(_AGENT_FILE, package_dir / _AGENT_FILE.name)
    shutil.copytree(_SKILLS_DIR, package_dir / "skills")

    instrument = package_dir / "bin/opentelemetry-instrument"
    lines = instrument.read_text(encoding="utf-8").splitlines()
    instrument.write_text(
        "\n".join(["#!/usr/bin/env python3", *lines[1:]]) + "\n",
        encoding="utf-8",
    )

    artifact = work_dir / "market_trends_skill_agent.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(package_dir.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(package_dir)
            info = zipfile.ZipInfo(relative.as_posix(), (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if relative == Path("bin/opentelemetry-instrument") else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED)
    return artifact


def _partition(identity_arn: str) -> str:
    return identity_arn.split(":", 2)[1]


def _trust_policy(
    partition: str,
    account_id: str,
    region: str,
    runtime_name: str,
) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {
                        "aws:SourceArn": (
                            f"arn:{partition}:bedrock-agentcore:{region}:{account_id}:runtime/{runtime_name}-*"
                        )
                    },
                },
            }
        ],
    }


def _execution_policy(
    partition: str,
    account_id: str,
    region: str,
    runtime_name: str,
) -> dict[str, Any]:
    log_group = f"arn:{partition}:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/runtimes/{runtime_name}-*"
    model_id = _MODEL_ID.removeprefix("us.")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeModel",
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": [
                    f"arn:{partition}:bedrock:{region}:{account_id}:inference-profile/{_MODEL_ID}",
                    f"arn:{partition}:bedrock:*::foundation-model/{model_id}",
                ],
            },
            {
                "Sid": "WriteRuntimeTelemetry",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:PutResourcePolicy",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                "Resource": [log_group, f"{log_group}:log-stream:*"],
            },
            {
                "Sid": "WriteTracesAndMetrics",
                "Effect": "Allow",
                "Action": [
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                    "cloudwatch:PutMetricData",
                ],
                "Resource": "*",
            },
        ],
    }


def _create_role(
    iam: Any,
    partition: str,
    account_id: str,
    region: str,
    runtime_name: str,
    suffix: str,
) -> tuple[str, str, str]:
    role_name = f"AgentCoreMarketTrendsSkills-{suffix}"
    policy_name = f"MarketTrendsSkills-{suffix}"
    role = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(_trust_policy(partition, account_id, region, runtime_name)),
        Description="Execution role for the Market Trends AgentSkills sample",
    )["Role"]
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(_execution_policy(partition, account_id, region, runtime_name)),
    )
    time.sleep(10)
    return str(role["Arn"]), role_name, policy_name


def _create_bucket(s3: Any, bucket: str, region: str) -> None:
    request: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        request["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**request)
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def _wait_until_ready(control: Any, agent_id: str) -> dict[str, Any]:
    for _ in range(40):
        runtime = control.get_agent_runtime(agentRuntimeId=agent_id)
        if not isinstance(runtime, dict):
            raise TypeError("get_agent_runtime returned a non-object response")
        status = runtime.get("status")
        print(f"Runtime status: {status}")
        if status == "READY":
            return runtime
        if status == "FAILED":
            raise RuntimeError(runtime.get("failureReason", "Runtime deployment failed"))
        time.sleep(15)
    raise TimeoutError("Runtime did not become ready within 10 minutes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=None)
    args = parser.parse_args()

    if _CONFIG_PATH.exists():
        parser.error(f"{_CONFIG_PATH.name} already exists. Clean up that deployment before creating another one.")
    _validate_source()

    region = args.region or Session().region_name or "us-east-1"
    sts = boto3.client("sts", region_name=region)
    identity = sts.get_caller_identity()
    account_id = str(identity["Account"])
    partition = _partition(str(identity["Arn"]))
    suffix = uuid.uuid4().hex[:8]
    runtime_name = f"market_trends_skills_{suffix}"
    role_arn, role_name, policy_name = _create_role(
        boto3.client("iam", region_name=region),
        partition,
        account_id,
        region,
        runtime_name,
        suffix,
    )

    bucket = f"agentcore-market-trends-{account_id}-{region}-{suffix}"
    key = "runtime/market_trends_skill_agent.zip"
    s3 = boto3.client("s3", region_name=region)
    _create_bucket(s3, bucket, region)

    with tempfile.TemporaryDirectory(prefix="market-trends-skills-") as temp:
        artifact = _build_artifact(Path(temp))
        s3.upload_file(str(artifact), bucket, key)

    control = boto3.client("bedrock-agentcore-control", region_name=region)
    created = control.create_agent_runtime(
        agentRuntimeName=runtime_name,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": key}},
                "runtime": "PYTHON_3_13",
                "entryPoint": [
                    "opentelemetry-instrument",
                    _AGENT_FILE.name,
                ],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        environmentVariables={"UNIFIED_TRACES_DESTINATION_ENABLED": "true"},
    )
    runtime = _wait_until_ready(control, str(created["agentRuntimeId"]))
    agent_id = str(runtime["agentRuntimeId"])
    config = {
        "account_id": account_id,
        "agent_id": agent_id,
        "agent_arn": str(runtime["agentRuntimeArn"]),
        "cw_log_group": f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT",
        "policy_name": policy_name,
        "region": region,
        "role_name": role_name,
        "s3_bucket": bucket,
        "s3_key": key,
        "service_name": f"{runtime_name}.DEFAULT",
        "skills": list(_SKILLS),
    }
    _CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    print("\nDeployment complete")
    print(f"Runtime ARN: {config['agent_arn']}")
    print(f"Unified log group: {config['cw_log_group']}")
    print(f"Config: {_CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
