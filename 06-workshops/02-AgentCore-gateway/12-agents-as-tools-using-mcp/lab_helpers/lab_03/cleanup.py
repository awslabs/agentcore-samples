"""
Lab 03: Remediation Agent Resource Cleanup

Removes all resources created during Lab 03:

AWS RESOURCES DELETED:
- AgentCore Gateway and all targets
- AgentCore Runtime (remediation-runtime)
- OAuth2 Credential Provider
- Secrets Manager secrets (m2m credentials)
- IAM roles (Runtime execution, Gateway service)
- CloudWatch logs

AWS RESOURCES PRESERVED:
- Parameter Store entries (put_parameter() now handles overwrites intelligently)
  • Re-run Section 7.3c to update with new runtime_arn/runtime_id after redeploying

LOCAL ARTIFACTS DELETED:
- agent-remediation.py
- Dockerfile
- .bedrock_agentcore.yaml
- .dockerignore
- Python cache (__pycache__/, *.pyc)

LOCAL ARTIFACTS PRESERVED:
- Lab-03-remediation-agent.ipynb (notebook file)
- lab_helpers/ module (preserved for reuse)
"""

import boto3
import json
import time
import shutil
import os
import logging
from lab_helpers.constants import PARAMETER_PATHS
from lab_helpers.lab_03.configure_logging import cleanup_runtime_logging
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


def cleanup_lab_03(region_name: str = "us-west-2", verbose: bool = True) -> None:
    """
    Clean up all Lab 03 resources (Runtime and Gateway).

    This function removes AWS resources and local artifacts created during Lab 03:

    AWS RESOURCES DELETED:
    1. AgentCore Gateway (and all targets)
    2. AgentCore Runtime (remediation-runtime)
    3. OAuth2 Credential Provider
    4. Secrets Manager secrets (m2m credentials)
    5. IAM roles (Runtime execution role, Gateway service role)
    6. CloudWatch logs

    AWS RESOURCES PRESERVED:
    - Parameter Store entries (intelligently overwritten on re-deploy)

    LOCAL ARTIFACTS DELETED:
    7. Generated files (agent-remediation.py, Dockerfile, .bedrock_agentcore.yaml, .dockerignore)
    8. Python cache (__pycache__/, *.pyc)

    Args:
        region_name: AWS region (default: us-west-2)
        verbose: Print detailed status messages (default: True)

    Returns:
        None (prints status to stdout)

    Example:
        from lab_helpers.lab_03.cleanup import cleanup_lab_03
        cleanup_lab_03(region_name="us-west-2", verbose=True)
    """
    print("🧹 Cleaning up Lab 03 resources...\n")
    print("=" * 70)

    if verbose:
        logging.basicConfig(level=logging.INFO)

    # Initialize clients
    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region_name)
    iam_client = boto3.client("iam")
    ssm_client = boto3.client("ssm", region_name=region_name)
    logs_client = boto3.client("logs", region_name=region_name)
    secrets_client = boto3.client("secretsmanager", region_name=region_name)

    # Debug: Find all parameters related to Lab 03
    if verbose:
        print("[DEBUG] Searching for Lab 03 parameters in Parameter Store...")
        try:
            response = ssm_client.describe_parameters(
                Filters=[
                    {
                        "Key": "Name",
                        "Values": ["lab-03", "lab03", "remediation", "aiml301"],
                    }
                ]
            )
            if response.get("Parameters"):
                print(f"  Found {len(response['Parameters'])} parameter(s):")
                for param in response["Parameters"]:
                    print(f"    • {param['Name']}")
            else:
                print("  No Lab 03 parameters found")
        except Exception as e:
            print(f"  ℹ Parameter search error: {e}")
        print()

    # 1. Delete OAuth2 Credential Provider
    print("[1/7] Deleting OAuth2 Credential Provider...")
    provider_deleted = False

    try:
        # Get provider ARN from Parameter Store
        try:
            response = ssm_client.get_parameter(Name=PARAMETER_PATHS["lab_03"]["oauth2_provider_arn"])
            provider_arn = response["Parameter"]["Value"]

            if provider_arn:
                # Extract provider name from ARN
                # ARN format: arn:aws:bedrock-agentcore:region:account:token-vault/default/oauth2credentialprovider/PROVIDER_NAME
                provider_name = provider_arn.split("/")[-1]

                if verbose:
                    print(f"  ℹ Found provider ARN: {provider_arn}")
                    print(f"  ℹ Extracted provider name: {provider_name}")

                try:
                    # Delete the provider using the correct 'name' parameter
                    agentcore_client.delete_oauth2_credential_provider(name=provider_name)
                    print(f"  ✓ OAuth2 credential provider deleted: {provider_name}")
                    provider_deleted = True
                except Exception as e:
                    error_str = str(e)
                    # Check if it's already deleted or doesn't exist
                    if "ResourceNotFoundException" in error_str or "does not exist" in error_str.lower():
                        print("  ✓ Provider already deleted or not found (ok)")
                        provider_deleted = True
                    else:
                        print(f"  ⚠ Failed to delete provider {provider_name}: {error_str}")

        except ssm_client.exceptions.ParameterNotFound:
            if verbose:
                print("  ℹ Provider ARN not found in Parameter Store (ok)")
            provider_deleted = True  # noqa: F841

    except Exception as e:
        print(f"  ⚠ OAuth2 cleanup error: {e}")

    # 1b. Delete Secrets Manager secrets created by OAuth2 credential provider
    print("[1b/8] Deleting Secrets Manager secrets...")
    try:
        # Paginate through secrets to find those created by the OAuth2 credential provider
        # OAuth2 provider creates secrets with pattern: bedrock-agentcore-identity!default/oauth2/aiml301-m2m-credentials-*
        paginator = secrets_client.get_paginator("list_secrets")
        pages = paginator.paginate()

        oauth2_secrets = []
        for page in pages:
            for secret in page.get("SecretList", []):
                secret_name = secret["Name"]
                # Match OAuth2 credential provider secrets
                if (
                    ("bedrock-agentcore-identity" in secret_name and "m2m-credentials" in secret_name)
                    or ("bedrock-agentcore-identity" in secret_name and "aiml301" in secret_name)
                    or "m2m-credentials" in secret_name
                ):
                    oauth2_secrets.append(secret)

        if oauth2_secrets:
            for secret in oauth2_secrets:
                secret_name = secret["Name"]
                try:
                    secrets_client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
                    print(f"  ✓ Secret deleted: {secret_name}")
                except Exception as e:
                    error_str = str(e)  # codeql[py/clear-text-logging-sensitive-data]
                    if "ResourceNotFoundException" not in error_str:
                        # Check if it's owned by bedrock-agentcore-identity (expected)
                        if "bedrock-agentcore-identity" in error_str:
                            print(
                                f"  ℹ Secret {secret_name} is service-owned - will be auto-deleted when provider is removed"
                            )
                        else:
                            print(
                                f"  ⚠ Failed to delete secret {secret_name}: {error_str}"
                            )  # codeql[py/clear-text-logging-sensitive-data]
        else:
            print("  ✓ No OAuth2 m2m credentials secrets found")  # codeql[py/clear-text-logging-sensitive-data]

    except Exception as e:
        print(f"  ⚠ Secrets Manager cleanup error: {e}")

    # 2. Delete Gateway (targets first, then gateway)
    print("[2/8] Deleting Gateway and targets...")
    try:
        # Find gateway by name
        gateways = agentcore_client.list_gateways()
        for gw in gateways.get("items", []):
            if "remediation-gateway" in gw["name"]:
                gateway_id = gw["gatewayId"]

                # Step 1: Delete targets
                try:
                    targets = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)
                    target_count = len(targets.get("items", []))

                    if target_count > 0:
                        print(f"  Deleting {target_count} target(s)...")
                        for target in targets.get("items", []):
                            target_id = target["targetId"]
                            agentcore_client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
                            print(f"    • Deleted target: {target_id}")

                        # Step 2: Verify targets are deleted with retry logic
                        print("  Verifying target deletion...")
                        max_retries = 5
                        retry_count = 0
                        targets_deleted = False

                        while retry_count < max_retries and not targets_deleted:
                            time.sleep(3)  # Wait for AWS propagation
                            remaining_targets = agentcore_client.list_gateway_targets(gatewayIdentifier=gateway_id)
                            remaining_count = len(remaining_targets.get("items", []))

                            if remaining_count == 0:
                                print("  ✓ All targets confirmed deleted")
                                targets_deleted = True
                            else:
                                retry_count += 1
                                if retry_count < max_retries:
                                    print(
                                        f"  ⏳ Retry {retry_count}/{max_retries - 1}: "
                                        f"{remaining_count} target(s) still present..."
                                    )
                                else:
                                    print(
                                        f"  ⚠ {remaining_count} target(s) still associated after {max_retries} retries"
                                    )
                    else:
                        print("  ✓ No targets found")
                        targets_deleted = True

                except Exception as e:
                    print(f"  ⚠ Target deletion: {e}")
                    targets_deleted = False

                # Step 3: Delete gateway (only if targets are confirmed deleted)
                try:
                    if targets_deleted:
                        agentcore_client.delete_gateway(gatewayIdentifier=gateway_id)
                        print("  ✓ Gateway deleted")
                    else:
                        print("  ⚠ Skipping gateway deletion - targets still present")
                        print("     Please try cleanup again in a few moments")
                except Exception as e:
                    print(f"  ⚠ Gateway deletion: {e}")

                break
        else:
            print("  ✓ Gateway not found (ok)")

    except Exception as e:
        print(f"  ⚠ Gateway lookup error: {e}")

    # 3. Delete Runtime and associated CloudWatch Logs Delivery
    print("[3/8] Deleting AgentCore Runtime...")
    try:
        runtime_deleted = False
        runtime_id_for_logging = None
        prefixes = [
            "aiml301_sre_agentcore",
            "aiml301-sre-agentcore",
            "aiml301",
            "lab-03",
        ]

        # First, try to get runtime info from Parameter Store
        for prefix in prefixes:
            if runtime_deleted:
                break

            try:
                # Try multiple parameter names (most specific first)
                param_names = [
                    f"/{prefix}/lab-03/runtime-id",  # Direct ID (most likely)
                    f"/{prefix}/lab-03/runtime-config",  # JSON with ID
                    f"/{prefix}/runtime-id",  # Fallback variations
                    f"/{prefix}/runtime-config",
                ]

                for param_name in param_names:
                    try:
                        response = ssm_client.get_parameter(Name=param_name)
                        param_value = response["Parameter"]["Value"]

                        if verbose:
                            print(f"  Found parameter: {param_name}")

                        # Try to parse as JSON first
                        runtime_id = None
                        try:
                            runtime_config = json.loads(param_value)
                            runtime_id = runtime_config.get("runtime_id")
                        except (json.JSONDecodeError, TypeError):
                            # If not JSON, assume it's the runtime ID directly
                            if param_value and param_value.strip():
                                runtime_id = param_value.strip()

                        if runtime_id:
                            print("  Found runtime ID: ****")
                            runtime_id_for_logging = runtime_id

                            # Clean up CloudWatch Logs Delivery BEFORE deleting runtime
                            try:
                                print("  Cleaning up CloudWatch Logs Delivery for runtime...")
                                cleanup_runtime_logging(runtime_id, region=region_name)
                            except Exception as e:
                                print(f"  ⚠ CloudWatch Logs Delivery cleanup warning: {e}")

                            try:
                                agentcore_client.delete_agent_runtime(agentRuntimeId=runtime_id)
                                print("  ✓ Runtime deletion initiated: ****")

                                # Wait for Runtime to be fully deleted
                                print("  ⏳ Waiting for Runtime deletion to complete...")
                                max_retries = 60
                                retry_count = 0

                                while retry_count < max_retries:
                                    time.sleep(5)
                                    try:
                                        status_check = agentcore_client.get_agent_runtime(agentRuntimeId=runtime_id)
                                        current_status = status_check.get("status", "UNKNOWN")
                                        retry_count += 1
                                        print(f"     Status: {current_status} (check {retry_count}/{max_retries})")

                                        if current_status == "DELETING":
                                            continue
                                    except agentcore_client.exceptions.ResourceNotFoundException:
                                        print("  ✓ Runtime fully deleted: ****")
                                        runtime_deleted = True
                                        break
                                    except Exception as e:
                                        if "not found" in str(e).lower():
                                            print("  ✓ Runtime fully deleted: ****")
                                            runtime_deleted = True
                                            break
                                        else:
                                            print(f"  ⚠ Error checking status: {e}")
                                            break

                                if not runtime_deleted:
                                    print(f"  ⚠ Runtime may still be deleting after {max_retries} retries")

                                break

                            except Exception as e:
                                error_str = str(e)
                                if (
                                    "ResourceNotFoundException" not in error_str
                                    and "does not exist" not in error_str.lower()
                                ):
                                    print(f"  ⚠ Runtime deletion error: {error_str}")

                    except ssm_client.exceptions.ParameterNotFound:
                        if verbose:
                            print(f"  Parameter not found: {param_name}")

            except Exception as e:
                if verbose:
                    print(f"  ℹ Parameter Store search ({prefix}): {e}")

        # Fallback: try to list and find runtimes
        if not runtime_deleted:
            if verbose:
                print("  Runtime not in Parameter Store, checking API...")

            try:
                runtimes = agentcore_client.list_agent_runtimes()
                all_items = runtimes.get("items", [])

                if verbose and all_items:
                    print(f"  Found {len(all_items)} runtime(s) via API")

                for rt in all_items:
                    runtime_name = rt["agentRuntimeName"].lower()
                    if "remediation" in runtime_name or "aiml301" in runtime_name:
                        runtime_id = rt["agentRuntimeId"]
                        runtime_id_for_logging = runtime_id  # noqa: F841
                        print(f"  Found runtime: {rt['agentRuntimeName']}")

                        # Clean up CloudWatch Logs Delivery BEFORE deleting runtime
                        try:
                            print("  Cleaning up CloudWatch Logs Delivery for runtime...")
                            cleanup_runtime_logging(runtime_id, region=region_name)
                        except Exception as e:
                            print(f"  ⚠ CloudWatch Logs Delivery cleanup warning: {e}")

                        try:
                            agentcore_client.delete_agent_runtime(agentRuntimeId=runtime_id)
                            print("  ✓ Runtime deletion initiated: ****")

                            # Wait for Runtime to be fully deleted
                            print("  ⏳ Waiting for Runtime deletion to complete...")
                            max_retries = 30
                            retry_count = 0

                            while retry_count < max_retries:
                                time.sleep(5)
                                try:
                                    status_check = agentcore_client.get_agent_runtime(agentRuntimeId=runtime_id)
                                    current_status = status_check.get("status", "UNKNOWN")
                                    retry_count += 1
                                    print(f"     Status: {current_status} (check {retry_count}/{max_retries})")

                                    if current_status == "DELETING":
                                        continue
                                except agentcore_client.exceptions.ResourceNotFoundException:
                                    print("  ✓ Runtime fully deleted: ****")
                                    runtime_deleted = True
                                    break
                                except Exception as e:
                                    if "not found" in str(e).lower():
                                        print("  ✓ Runtime fully deleted: ****")
                                        runtime_deleted = True
                                        break
                                    else:
                                        print(f"  ⚠ Error checking status: {e}")
                                        break

                            if not runtime_deleted:
                                print(f"  ⚠ Runtime may still be deleting after {max_retries} retries")

                            break
                        except Exception as e:
                            print(f"  ⚠ Runtime deletion failed: {e}")

            except Exception as e:
                if verbose:
                    print(f"  ℹ API lookup error: {e}")

        if not runtime_deleted:
            print("  ✓ Runtime not found (ok)")

    except Exception as e:
        print(f"  ⚠ Runtime cleanup error: {e}")

    # 3b. Delete Custom Code Interpreter
    print("[3b/8] Deleting Custom Code Interpreter...")
    try:
        # Try to get from SSM first
        interpreter_id = None
        try:
            response = ssm_client.get_parameter(Name=PARAMETER_PATHS["lab_03"]["code_interpreter_id"])
            interpreter_id = response["Parameter"]["Value"]
            print(f"  Found interpreter ID from SSM: {interpreter_id}")
        except ssm_client.exceptions.ParameterNotFound:
            if verbose:
                print("  Interpreter ID not in SSM, checking API...")

        # If not in SSM, list and find
        if not interpreter_id:
            list_response = agentcore_client.list_code_interpreters()
            for item in list_response.get("codeInterpreterSummaries", []):
                if "aiml301" in item.get("name", "").lower() and "custom" in item.get("name", "").lower():
                    interpreter_id = item["codeInterpreterId"]
                    print(f"  Found interpreter via API: {interpreter_id}")
                    break

        if interpreter_id:
            try:
                agentcore_client.delete_code_interpreter(codeInterpreterId=interpreter_id)
                print(f"  ✓ Code interpreter deleted: {interpreter_id}")
            except Exception as e:
                if "ResourceNotFoundException" in str(e) or "not found" in str(e).lower():
                    print("  ✓ Code interpreter already deleted (ok)")
                else:
                    print(f"  ⚠ Failed to delete code interpreter: {e}")
        else:
            print("  ✓ Code interpreter not found (ok)")
    except Exception as e:
        print(f"  ⚠ Code interpreter cleanup error: {e}")

    # 4. Delete IAM roles
    print("[4/8] Deleting IAM roles...")

    # Delete Custom Runtime execution role
    try:
        _delete_role(iam_client, "aiml301_sre_agentcore_CustomRuntimeRole")
        print("  ✓ Custom Runtime execution role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Custom Runtime execution role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Custom Runtime role: {e}")

    # Delete Code Interpreter execution role
    try:
        _delete_role(iam_client, "aiml301_sre_agentcore-CodeInterpreterRole")
        print("  ✓ Code Interpreter execution role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Code Interpreter execution role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Code Interpreter role: {e}")

    # Delete old Runtime execution role (if exists)
    try:
        _delete_role(iam_client, "aiml301-agentcore-remediation-role")
        print("  ✓ Old Runtime execution role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Old Runtime execution role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Old Runtime role: {e}")

    # Delete Gateway service role
    try:
        _delete_role(iam_client, "aiml301-remediation-gateway-role")
        print("  ✓ Gateway service role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Gateway service role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Gateway role: {e}")

    # 5. Parameter Store entries (PRESERVED for reuse)
    print("[5/8] Parameter Store entries...")
    print("  ✓ Preserved (put_parameter() now handles overwrites intelligently)")
    print("  ℹ Run Section 7.3c again to update values with latest ARN/ID")

    # 6. Delete CloudWatch logs
    print("[6/8] Deleting CloudWatch log groups...")
    try:
        # Find and delete log groups matching pattern
        logs_pattern = "/aws/bedrock-agentcore/runtime"
        log_groups = logs_client.describe_log_groups(logGroupNamePrefix=logs_pattern)

        for lg in log_groups.get("logGroups", []):
            if "remediation" in lg["logGroupName"].lower():
                try:
                    logs_client.delete_log_group(logGroupName=lg["logGroupName"])
                    print(f"  ✓ Log group deleted: {lg['logGroupName']}")
                except Exception as e:
                    print(f"  ⚠ Failed to delete {lg['logGroupName']}: {e}")

    except logs_client.exceptions.ResourceNotFoundException:
        print("  ✓ No log groups found (ok)")
    except Exception as e:
        print(f"  ⚠ Log group cleanup: {e}")

    # 7. Delete local generated files
    print("[7/8] Deleting local artifacts...")
    try:
        # Get current working directory
        cwd = os.getcwd()

        # Files to delete
        files_to_delete = [
            os.path.join(cwd, "agent-remediation.py"),
            os.path.join(cwd, "Dockerfile"),
            os.path.join(cwd, ".bedrock_agentcore.yaml"),
            os.path.join(cwd, ".dockerignore"),
        ]

        deleted_count = 0
        for file_path in files_to_delete:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    print(f"  ✓ Deleted: {os.path.basename(file_path)}")
                    deleted_count += 1
                except Exception as e:
                    print(f"  ⚠ Failed to delete {os.path.basename(file_path)}: {e}")

        # Clean up Python cache
        pycache_paths = [
            os.path.join(cwd, "__pycache__"),
            os.path.join(cwd, "agent_remediation.cpython-*.pyc"),
        ]

        for pycache in pycache_paths:
            if "__pycache__" in pycache and os.path.isdir(pycache):
                try:
                    shutil.rmtree(pycache)
                    print("  ✓ Deleted: __pycache__")
                except Exception as e:
                    print(f"  ⚠ Failed to delete __pycache__: {e}")

        if deleted_count == 0:
            print("  ✓ No local artifacts found (ok)")

    except Exception as e:
        print(f"  ⚠ Local cleanup: {e}")

    # 7. Delete local generated files
    print("[8/8] Deleting s3 bucket containing remediation plans...")
    s3_client = boto3.client("s3", region_name=region_name)
    s3_resource = boto3.resource("s3", region_name=region_name)

    parameter_name = "/aiml301_sre_workshop/remediation_s3_bucket"

    try:
        # Get bucket name from Parameter Store
        response = ssm_client.get_parameter(Name=parameter_name)
        bucket_name = response["Parameter"]["Value"]
        print(f"Found bucket name in Parameter Store: {bucket_name}")

        # Empty and delete the bucket
        bucket = s3_resource.Bucket(bucket_name)
        print(f"Emptying bucket: {bucket_name}")
        bucket.objects.all().delete()
        bucket.object_versions.all().delete()

        print(f"Deleting bucket: {bucket_name}")
        s3_client.delete_bucket(Bucket=bucket_name)
        print(f"Successfully deleted bucket: {bucket_name}")

        # Delete the parameter
        ssm_client.delete_parameter(Name=parameter_name)
        print(f"Deleted parameter: {parameter_name}")

        print("Cleanup complete!")

    except ClientError as e:
        print(f"Error during cleanup: {e}")
        raise

    print("\n" + "=" * 70)
    print("✅ Lab 03 cleanup complete")
    print("\nYou can now re-run Lab 03 from Section 1")


def _delete_role(iam_client, role_name: str) -> None:
    """
    Helper: Detach all policies and delete role.

    Args:
        iam_client: IAM boto3 client
        role_name: Name of IAM role to delete
    """
    # Detach managed policies
    policies = iam_client.list_attached_role_policies(RoleName=role_name)
    for policy in policies.get("AttachedPolicies", []):
        iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

    # Delete inline policies
    inline_policies = iam_client.list_role_policies(RoleName=role_name)
    for policy_name in inline_policies.get("PolicyNames", []):
        iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

    # Delete role
    iam_client.delete_role(RoleName=role_name)


if __name__ == "__main__":
    from lab_helpers.config import AWS_REGION

    print("Lab 03: Cleanup All Resources")
    print("=" * 70)
    print("\nWARNING: This will delete:")
    print("\nAWS RESOURCES DELETED:")
    print("  • AgentCore Gateway and all targets")
    print("  • AgentCore Runtime")
    print("  • OAuth2 Credential Provider")
    print("  • Secrets Manager secrets (m2m credentials)")
    print("  • IAM roles (Runtime, Gateway)")
    print("  • CloudWatch logs")
    print("\nAWS RESOURCES PRESERVED:")
    print("  ✓ Parameter Store entries (will be updated on re-deploy)")
    print("\nLOCAL FILES DELETED:")
    print("  • agent-remediation.py")
    print("  • Dockerfile")
    print("  • .bedrock_agentcore.yaml")
    print("  • .dockerignore")
    print("  • Python cache (__pycache__/)")
    print("\nThis action cannot be undone.\n")

    confirm = input("Are you sure? Type 'yes' to proceed: ")
    if confirm.lower() == "yes":
        cleanup_lab_03(region_name=AWS_REGION, verbose=True)
    else:
        print("Cleanup cancelled")
