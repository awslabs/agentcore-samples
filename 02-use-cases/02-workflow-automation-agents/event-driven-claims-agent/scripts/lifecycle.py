#!/usr/bin/env python3
"""Ownership-checked fresh-v1 lifecycle. Cloud operations require --allow-cloud.

No regional orphan sweeps, bootstrap, observability configuration, seeding or
notifications. A failed/uncertain operation keeps its journal and stops.
"""

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
OWNER_TAG = "claims:owner"
CREDENTIAL = "cognito-gateway-m2m"
PROVIDER_READY_ATTEMPTS = 24
PROVIDER_READY_INTERVAL_SECONDS = 5
PROVIDER_READY_TIMEOUT_SECONDS = 120


def save(root, state):
    path = root / ".claims-v1.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def load(root):
    state = json.loads((root / ".claims-v1.json").read_text())
    spec = json.loads((root / "agentcore/agentcore.json").read_text())
    targets = json.loads((root / "agentcore/aws-targets.json").read_text())
    expected = [{"name": state["target"], "account": state["account"], "region": state["region"]}]
    if state["version"] != 1 or spec["version"] != 2 or spec["name"] != state["project"] or targets != expected:
        raise ValueError("Ownership/config/target mismatch; no adoption supported")
    if spec.get("migratedFrom") is not None:
        raise ValueError("Legacy provider-name migration metadata is not allowed in a fresh stack")
    credentials = spec.get("credentials", [])
    allowed_fields = {"authorizerType", "name", "discoveryUrl", "scopes", "vendor", "clientId"}
    if (
        len(credentials) != 1
        or credentials[0].get("name") != CREDENTIAL
        or credentials[0].get("authorizerType") != "OAuthCredentialProvider"
        or credentials[0].get("vendor") != "CustomOauth2"
        or credentials[0].get("scopes") != ["agentcore/invoke"]
        or set(credentials[0]) - allowed_fields
    ):
        raise ValueError("Only the dedicated guided OAuth credential is supported; no references/extra providers")
    if state.get("client_id") and (
        credentials[0].get("clientId") != state["client_id"]
        or credentials[0].get("discoveryUrl") != state.get("discovery_url")
    ):
        raise ValueError("Declared credential differs from the owned provider")
    if state["provider_name"] != f"{state['project']}_{state['target']}_{CREDENTIAL}":
        raise ValueError("Provider identity mismatch")
    if (root / ".cognito-state.json").exists():
        raise ValueError("Legacy Cognito state must not be adopted")
    return state


def absent(call, *args, missing=("ResourceNotFoundException",), **kwargs):
    try:
        return call(*args, **kwargs)
    except ClientError as error:
        if error.response["Error"]["Code"] in missing:
            return None
        raise  # AccessDenied, throttles and transport errors are never absence.


def pages(client, method, key, token="NextToken", **kwargs):
    while True:
        response = getattr(client, method)(**kwargs)
        yield from response.get(key, [])
        next_token = response.get(token)
        if not next_token:
            return
        kwargs[token] = next_token


def assert_owner(tags, state):
    if tags.get(OWNER_TAG) != state["owner"]:
        raise ValueError("Resource ownership tag mismatch; refusing reuse/deletion")


def setup(root, state, clients):
    if state["phase"] != "configured":
        raise ValueError("Setup is fresh-only. Partial/previous setup needs journal review, not adoption")
    cognito, control = clients("cognito-idp"), clients("bedrock-agentcore-control")
    if any(
        pool["Name"] == state["pool_name"]
        for pool in pages(cognito, "list_user_pools", "UserPools", token="NextToken", MaxResults=60)
    ):
        raise ValueError("Unowned Cognito pool name collision")
    if cognito.describe_user_pool_domain(Domain=state["domain"]).get("DomainDescription", {}).get("UserPoolId"):
        raise ValueError("Cognito domain collision")
    if absent(control.get_oauth2_credential_provider, name=state["provider_name"]):
        raise ValueError("Unowned OAuth provider collision")
    state["phase"] = "creating-pool"
    save(root, state)
    pool = cognito.create_user_pool(PoolName=state["pool_name"], UserPoolTags={OWNER_TAG: state["owner"]})["UserPool"]
    state["pool_id"] = pool["Id"]
    state["pool_arn"] = pool["Arn"]
    save(root, state)
    cognito.create_resource_server(
        UserPoolId=state["pool_id"],
        Identifier="agentcore",
        Name="AgentCore Gateway",
        Scopes=[{"ScopeName": "invoke", "ScopeDescription": "Invoke claims tools"}],
    )
    cognito.create_user_pool_domain(Domain=state["domain"], UserPoolId=state["pool_id"])
    state["domain_created"] = True
    save(root, state)
    client = cognito.create_user_pool_client(
        UserPoolId=state["pool_id"],
        ClientName=f"{state['project']}-M2M",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=["agentcore/invoke"],
        AllowedOAuthFlowsUserPoolClient=True,
    )["UserPoolClient"]
    state["client_id"] = client["ClientId"]
    state["discovery_url"] = (
        f"https://cognito-idp.{state['region']}.amazonaws.com/{state['pool_id']}/.well-known/openid-configuration"
    )
    state["phase"] = "creating-provider"
    save(root, state)
    # The secret is passed in memory only. Never stored in .env, argv or the ledger.
    provider = control.create_oauth2_credential_provider(
        name=state["provider_name"],
        credentialProviderVendor="CustomOauth2",
        tags={OWNER_TAG: state["owner"]},
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": state["discovery_url"]},
                "clientId": client["ClientId"],
                "clientSecret": client["ClientSecret"],
            }
        },
    )
    # Persist every returned ID before polling or interpreting optional status.
    # A failed/malformed readiness response must not erase ownership evidence.
    state["provider_arn"] = provider["credentialProviderArn"]
    secret_arn = provider.get("clientSecretArn", {}).get("secretArn")
    if secret_arn:
        state["secret_arn"] = secret_arn
    state["provider_status"] = provider.get("status")
    state["phase"] = "waiting-provider"
    save(root, state)
    if not secret_arn:
        raise ValueError("Provider returned no secret ARN; ownership journal retained")
    if provider.get("status") not in (None, "CREATING", "READY"):
        raise ValueError("Provider creation is not ready; ownership journal retained")
    wait_for_provider_ready(root, state, control)
    directory = root / "agentcore/.cli"
    directory.mkdir(exist_ok=True)
    with (directory / "deployed-state.json").open("x") as stream:
        json.dump(
            {
                "targets": {
                    state["target"]: {
                        "resources": {
                            "credentials": {
                                CREDENTIAL: {
                                    "credentialProviderArn": state["provider_arn"],
                                    "clientSecretArn": state["secret_arn"],
                                    "authorizerType": "OAuthCredentialProvider",
                                }
                            }
                        }
                    }
                }
            },
            stream,
            indent=2,
        )
    spec_path = root / "agentcore/agentcore.json"
    spec = json.loads(spec_path.read_text())
    spec["credentials"][0]["discoveryUrl"] = state["discovery_url"]
    spec["credentials"][0]["clientId"] = state["client_id"]
    spec_path.write_text(json.dumps(spec, indent=2) + "\n")
    state["phase"] = "auth-ready"
    save(root, state)


def wait_for_provider_ready(root, state, control):
    """Bounded polling of the genuine optional status; absence is not readiness.

    Only initial CREATING may be retried. Failed, unknown, missing and updating
    states stop without rotating/deleting the provider or discarding its IDs.
    Control clients also have bounded per-request timeouts in main().
    """
    deadline = time.monotonic() + PROVIDER_READY_TIMEOUT_SECONDS
    for attempt in range(PROVIDER_READY_ATTEMPTS):
        if time.monotonic() >= deadline:
            break
        provider = control.get_oauth2_credential_provider(name=state["provider_name"])
        state["provider_status"] = provider.get("status")
        save(root, state)
        if time.monotonic() >= deadline:
            break
        verify_provider_identity(state, control, provider)
        if provider.get("status") == "READY":
            if time.monotonic() >= deadline:
                break
            return
        if provider.get("status") != "CREATING":
            raise ValueError("Provider readiness failed or is unknown; ownership journal retained")
        if attempt + 1 < PROVIDER_READY_ATTEMPTS:
            time.sleep(min(PROVIDER_READY_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    raise TimeoutError("Provider did not become READY within the polling bound; ownership journal retained")


def verify_auth(state, clients):
    pool = clients("cognito-idp").describe_user_pool(UserPoolId=state["pool_id"])["UserPool"]
    assert_owner(pool.get("UserPoolTags", {}), state)
    control = clients("bedrock-agentcore-control")
    provider = control.get_oauth2_credential_provider(name=state["provider_name"])
    verify_provider_identity(state, control, provider)
    if provider.get("status") != "READY":
        raise ValueError("Provider is not proven READY; deployment refused")


def verify_provider_identity(state, control, provider):
    if provider["credentialProviderArn"] != state["provider_arn"]:
        raise ValueError("Provider ARN mismatch")
    assert_owner(control.list_tags_for_resource(resourceArn=state["provider_arn"])["tags"], state)
    if provider.get("clientSecretArn", {}).get("secretArn") != state["secret_arn"]:
        raise ValueError("Provider secret ARN drift; explicit ownership reconciliation required")
    config = provider["oauth2ProviderConfigOutput"]["customOauth2ProviderConfig"]
    if config["clientId"] != state["client_id"] or config["oauthDiscovery"]["discoveryUrl"] != state["discovery_url"]:
        raise ValueError("Provider configuration drift")


def assert_fresh_resources(state, clients):
    """Check named resources whose Create APIs can reuse existing physical names."""
    prefix = f"{state['project']}-{state['target']}"
    for topic in pages(clients("sns"), "list_topics", "Topics"):
        if topic["TopicArn"].split(":")[-1] == f"{prefix}-HumanReview":
            raise ValueError("SNS topic collision")
    checks = [
        ("dynamodb", "describe_table", {"TableName": f"{prefix}-{name}"}, ("ResourceNotFoundException",))
        for name in ("Claims", "Policies", "Reviews")
    ]
    checks += [
        ("lambda", "get_function", {"FunctionName": f"{prefix}-{name}"}, ("ResourceNotFoundException",))
        for name in (
            "PolicyLookup",
            "CreateClaim",
            "HumanReview",
            "Notification",
            "ListPending",
            "ResolveClaim",
            "Trigger",
        )
    ]
    checks += [
        (
            "s3",
            "head_bucket",
            {"Bucket": f"{prefix.lower()}-inbox-{state['account']}-{state['region']}"},
            ("404", "NoSuchBucket"),
        ),
        ("sqs", "get_queue_url", {"QueueName": f"{prefix}-TriggerDLQ"}, ("AWS.SimpleQueueService.NonExistentQueue",)),
        ("events", "describe_rule", {"Name": f"{prefix}-InboxTrigger"}, ("ResourceNotFoundException",)),
    ]
    for service, method, args, missing in checks:
        if absent(getattr(clients(service), method), missing=missing, **args) is not None:
            raise ValueError(f"Unowned {service} resource collision: {args}")
    if clients("cloudwatch").describe_alarms(AlarmNames=[f"{prefix}-FailedClaims"]).get("MetricAlarms"):
        raise ValueError("Alarm collision")


def stack_or_none(cfn, name):
    try:
        return cfn.describe_stacks(StackName=name)["Stacks"][0]
    except ClientError as error:
        if (
            error.response["Error"]["Code"] == "ValidationError"
            and "does not exist" in error.response["Error"]["Message"]
        ):
            return None
        raise


def deploy(root, state, clients, cli):
    if state["phase"] not in ("auth-ready", "deployed"):
        raise ValueError("Partial/uncertain lifecycle; review ledger before retrying")
    # CLI v1 otherwise updates or adopts providers by name. Only permit verified
    # existing owned providers, with no secret input that could trigger an update.
    if (root / "agentcore/.env.local").exists() or any(k.startswith("CREDENTIAL_") for k in os.environ):
        raise ValueError("Remove credential overrides from this invocation; lifecycle never rotates providers")
    verify_auth(state, clients)
    cfn = clients("cloudformation")
    name = f"AgentCore-{state['project']}-{state['target']}"
    existing = stack_or_none(cfn, name)
    if existing:
        if existing["StackId"] != state.get("stack_arn"):
            raise ValueError("Unowned stack collision")
        assert_owner({t["Key"]: t["Value"] for t in existing.get("Tags", [])}, state)
    else:
        if state.get("stack_arn"):
            raise ValueError("Recorded stack disappeared; refusing automatic recreation")
        assert_fresh_resources(state, clients)
    state["phase"] = "deploying"
    save(root, state)
    subprocess.run(
        [cli, "deploy", "--target", state["target"], "--skip-bootstrap", "--yes"],
        cwd=root,
        check=True,
        env={**os.environ, "HOME": os.environ["CLAIMS_CLI_HOME"], "AGENTCORE_DISABLE_KEYCHAIN": "1"},
    )
    stack = stack_or_none(cfn, name)
    if not stack:
        raise ValueError("Deploy returned without a stack")
    assert_owner({t["Key"]: t["Value"] for t in stack.get("Tags", [])}, state)
    state["stack_arn"] = stack["StackId"]
    state["phase"] = "deployed"
    save(root, state)


def verify_residue(state, clients):
    """Check exact service-managed IDs that can outlive CFN DELETE_COMPLETE."""
    for resource in state.get("resources", []):
        physical = resource.get("PhysicalResourceId")
        if not physical:
            continue
        if resource["ResourceType"] == "AWS::KMS::Key":
            key = absent(clients("kms").describe_key, KeyId=physical, missing=("NotFoundException",))
            if key is not None:
                raise ValueError("KMS key still exists (possibly PendingDeletion); ledger retained")
        if resource["ResourceType"] == "AWS::Logs::LogGroup":
            groups = clients("logs").describe_log_groups(logGroupNamePrefix=physical).get("logGroups", [])
            if any(group["logGroupName"] == physical for group in groups):
                raise ValueError("Recorded log group still exists; ledger retained")
    for arn in state.get("workload_identity_arns", []):
        if absent(clients("bedrock-agentcore-control").get_workload_identity, name=arn.split("/")[-1]) is not None:
            raise ValueError("Service-managed workload identity remains; exact-ID review required")


def cleanup(root, state, clients):
    # No name-based recovery on uncertainty: a timed-out create can have succeeded.
    if state["phase"] not in ("deployed", "auth-ready", "deleting-stack", "deleting-auth"):
        raise ValueError("Partial setup/deploy requires exact-ID journal recovery; no sweep permitted")
    from identity_tracing import cleanup as cleanup_identity_tracing

    cleanup_identity_tracing(root, state, clients)
    cfn = clients("cloudformation")
    if state.get("stack_arn"):
        stack = stack_or_none(cfn, state["stack_arn"])
        if stack and stack["StackStatus"] != "DELETE_COMPLETE":
            assert_owner({t["Key"]: t["Value"] for t in stack.get("Tags", [])}, state)
            # Keep every physical ID for failed deletes and retained resources.
            state["resources"] = list(
                pages(cfn, "list_stack_resources", "StackResourceSummaries", StackName=state["stack_arn"])
            )
            # JSON ledger records just identities/status, not timestamps from boto3.
            state["resources"] = [
                {
                    k: r[k]
                    for k in ("LogicalResourceId", "PhysicalResourceId", "ResourceType", "ResourceStatus")
                    if k in r
                }
                for r in state["resources"]
            ]
            identities = set(state.get("workload_identity_arns", []))
            for resource in state["resources"]:
                kind = resource["ResourceType"]
                physical = resource.get("PhysicalResourceId", "").split("/")[-1]
                if kind not in ("AWS::BedrockAgentCore::Runtime", "AWS::BedrockAgentCore::Gateway"):
                    continue
                control = clients("bedrock-agentcore-control")
                details = (
                    absent(control.get_agent_runtime, agentRuntimeId=physical)
                    if kind.endswith("Runtime")
                    else absent(control.get_gateway, gatewayIdentifier=physical)
                )
                arn = (details or {}).get("workloadIdentityDetails", {}).get("workloadIdentityArn")
                if arn:
                    identities.add(arn)
            state["workload_identity_arns"] = sorted(identities)
            state["phase"] = "deleting-stack"
            save(root, state)
            cfn.delete_stack(StackName=state["stack_arn"])
            cfn.get_waiter("stack_delete_complete").wait(
                StackName=state["stack_arn"], WaiterConfig={"Delay": 10, "MaxAttempts": 60}
            )
        remaining = list(pages(cfn, "list_stack_resources", "StackResourceSummaries", StackName=state["stack_arn"]))
        if any(r["ResourceStatus"] != "DELETE_COMPLETE" for r in remaining):
            raise ValueError("Retained/partial stack cleanup; ledger preserved, auth not deleted")
    state["phase"] = "deleting-auth"
    save(root, state)
    control, cognito = clients("bedrock-agentcore-control"), clients("cognito-idp")
    provider = absent(control.get_oauth2_credential_provider, name=state["provider_name"])
    if provider:
        if provider["credentialProviderArn"] != state["provider_arn"]:
            raise ValueError("Provider ARN mismatch")
        assert_owner(control.list_tags_for_resource(resourceArn=state["provider_arn"])["tags"], state)
        if provider.get("clientSecretArn", {}).get("secretArn") != state["secret_arn"]:
            raise ValueError("Provider secret ARN drift; refusing ambiguous cleanup")
        control.delete_oauth2_credential_provider(name=state["provider_name"])
    if absent(control.get_oauth2_credential_provider, name=state["provider_name"]) is not None:
        raise ValueError("Provider deletion not yet verified; retry cleanup")
    pool = absent(cognito.describe_user_pool, UserPoolId=state["pool_id"])
    if pool:
        assert_owner(pool["UserPool"].get("UserPoolTags", {}), state)
        domain = cognito.describe_user_pool_domain(Domain=state["domain"]).get("DomainDescription", {})
        if domain.get("UserPoolId"):
            if domain["UserPoolId"] != state["pool_id"]:
                raise ValueError("Domain ownership mismatch")
            cognito.delete_user_pool_domain(Domain=state["domain"], UserPoolId=state["pool_id"])
        cognito.delete_user_pool(UserPoolId=state["pool_id"])
    if absent(cognito.describe_user_pool, UserPoolId=state["pool_id"]) is not None:
        raise ValueError("Pool deletion not yet verified; retry cleanup")
    # Identity may retain its secret. Do not delete it by guessed ARN or claim it gone.
    secret = absent(clients("secretsmanager").describe_secret, SecretId=state["secret_arn"])
    if secret is not None:
        raise ValueError("Identity secret still exists (including scheduled deletion); exact-ID follow-up required")
    verify_residue(state, clients)
    state["phase"] = "deleted"
    save(root, state)  # Never erase evidence or deployment identity, even on success.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation", choices=("setup", "deploy", "cleanup", "identity-tracing", "disable-identity-tracing")
    )
    parser.add_argument("--allow-cloud", action="store_true")
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error("Cloud operations require explicit --allow-cloud; offline tests must use stubbed clients")
    state = load(ROOT)
    cli = None
    if args.operation == "deploy":
        cli = os.environ["AGENTCORE_CLI"]  # Never fall back to a global/prerelease CLI.
        # CLI 1.2.0 reads HOME/.agentcore/config.json, NOT AGENTCORE_CONFIG_DIR.
        config = Path(os.environ["CLAIMS_CLI_HOME"]) / ".agentcore/config.json"
        prefs = json.loads(config.read_text())
        if prefs.get("disableDependencyManagement") is not True:
            raise ValueError("Custom CDK app requires disableDependencyManagement=true in its dedicated CLI HOME")
        if prefs.get("transactionSearch") is not False:
            raise ValueError("Account-level Transaction Search changes must be disabled")
        cli_env = {**os.environ, "HOME": os.environ["CLAIMS_CLI_HOME"], "AGENTCORE_DISABLE_KEYCHAIN": "1"}
        if subprocess.check_output([cli, "--version"], text=True, env=cli_env).strip() != "1.2.0":
            raise ValueError("CLI 1.2.0 is required")
    import boto3

    session = boto3.Session(region_name=state["region"])

    def clients(service):
        if service == "bedrock-agentcore-control":
            return session.client(
                service, config=Config(connect_timeout=5, read_timeout=10, retries={"total_max_attempts": 1})
            )
        return session.client(service)

    if clients("sts").get_caller_identity()["Account"] != state["account"]:
        raise ValueError("AWS account mismatch")
    if args.operation == "deploy":
        deploy(ROOT, state, clients, cli)
    elif args.operation == "identity-tracing":
        from identity_tracing import setup as setup_identity_tracing

        setup_identity_tracing(ROOT, state, clients)
    elif args.operation == "disable-identity-tracing":
        from identity_tracing import cleanup as cleanup_identity_tracing

        cleanup_identity_tracing(ROOT, state, clients)
    else:
        {"setup": setup, "cleanup": cleanup}[args.operation](ROOT, state, clients)


if __name__ == "__main__":
    main()
