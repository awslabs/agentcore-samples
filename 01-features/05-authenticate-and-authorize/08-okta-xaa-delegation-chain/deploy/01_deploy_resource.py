"""Deploy the todo resource API to Lambda behind an API Gateway HTTP API.

The API validates `T_tool` against AS 2's JWKS and nothing else -- it issues and
exchanges nothing.

Why API Gateway rather than a Lambda Function URL: some accounts' SCPs block Function
URLs for both NONE and AWS_IAM auth, which shows up as a bare 403 that looks like a
token problem. An HTTP API avoids that class of confusion.

Writes RESOURCE_API_URL and RESOURCE_LAMBDA_NAME to .env.

    python deploy/01_deploy_resource.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    SAMPLE_ROOT,
    account_id,
    clients,
    ensure_lambda,
    ensure_role,
    env,
    load_env,
    must_env,
    region,
    resource_lambda_name,
    resource_role_name,
    save_env,
    set_log_retention,
    zip_files,
)

APP_DIR = SAMPLE_ROOT / "resource-app"


def build_bundle(aws) -> bytes:
    """Zip main.py + lambda_handler.py with their pure-Python dependencies.

    mangum, fastapi, pydantic and pyjwt all ship wheels that work on the Lambda
    runtime, so they are installed into a staging directory and zipped in. Anything
    with a compiled extension would need --platform manylinux2014_x86_64.
    """
    import shutil
    import subprocess
    import tempfile
    import zipfile

    stage = Path(tempfile.mkdtemp(prefix="xaa-resource-"))
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--platform",
                "manylinux2014_x86_64",
                "--only-binary=:all:",
                "--python-version",
                "3.12",
                "--target",
                str(stage),
                "-r",
                str(APP_DIR / "requirements.txt"),
            ],
            check=True,
        )
        for name in ("main.py", "lambda_handler.py"):
            shutil.copy(APP_DIR / name, stage / name)
        buf = Path(tempfile.mkdtemp(prefix="xaa-zip-")) / "bundle.zip"
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(stage.rglob("*")):
                if path.is_file() and "__pycache__" not in path.parts:
                    z.write(path, path.relative_to(stage))
        data = buf.read_bytes()
        print(f"  bundle: {len(data) / 1_000_000:.1f} MB")
        return data
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def ensure_http_api(aws, fn_arn: str, name: str) -> str:
    from botocore.exceptions import ClientError

    for api in aws["apigw"].get_apis()["Items"]:
        if api["Name"] == name:
            print(f"  • reusing HTTP API {name}")
            return api["ApiEndpoint"]
    api = aws["apigw"].create_api(Name=name, ProtocolType="HTTP", Target=fn_arn, RouteKey="ANY /{proxy+}")
    try:
        aws["lam"].add_permission(
            FunctionName=fn_arn,
            StatementId="apigw-invoke",
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=f"arn:aws:execute-api:{region()}:{account_id()}:{api['ApiId']}/*",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceConflictException":
            raise
    print(f"  ✓ created HTTP API {name}")
    return api["ApiEndpoint"]


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_env()
    aws = clients()
    fn_name = resource_lambda_name()
    issuer = must_env("RESOURCE_AS_ISSUER", "Run deploy/00_create_okta_apps.py first.")

    print(f"account {account_id()} region {region()}\n")
    print("[1/3] Execution role")
    role = ensure_role(
        aws["iam"],
        resource_role_name(),
        "lambda.amazonaws.com",
        None,
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )

    print("\n[2/3] Lambda")
    code = build_bundle(aws)
    fn_arn = ensure_lambda(
        aws["lam"],
        fn_name,
        code,
        role,
        "lambda_handler.handler",
        {
            "RESOURCE_AS_ISSUER": issuer,
            "RESOURCE_AUDIENCE": env("RESOURCE_AUDIENCE", "api://todo"),
            "RESOURCE_SCOPE": env("RESOURCE_SCOPE", "todos.read"),
            # Optional hardening: require the acting agent to be ours, not merely any
            # agent Okta vouches for. Set once AI_AGENT_CLIENT_ID is known.
            "EXPECTED_ACT_SUB": env("AI_AGENT_CLIENT_ID", ""),
        },
    )
    set_log_retention(aws["logs"], fn_name)

    print("\n[3/3] HTTP API")
    endpoint = ensure_http_api(aws, fn_arn, f"{fn_name}-api")

    save_env(RESOURCE_API_URL=endpoint, RESOURCE_LAMBDA_NAME=fn_name)
    print(f"\n  ✓ RESOURCE_API_URL={endpoint}")
    print(f"    health check: curl {endpoint}/health")
    print("\n  Next: python deploy/02_create_gateway.py")


if __name__ == "__main__":
    main()
