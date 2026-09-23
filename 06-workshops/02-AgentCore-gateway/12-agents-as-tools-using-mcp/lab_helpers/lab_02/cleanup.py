"""
Lab 02: Resource Cleanup

Removes all resources created during Lab 02:

AWS RESOURCES:
- AgentCore Gateway and all targets
- Lambda function (aiml301-diagnostic-agent)
- ECR repository (aiml301-diagnostic-agent)
- S3 bucket and all deployment packages
- IAM roles (Lambda execution, Gateway service)
- Parameter Store entries
- CloudWatch logs

LOCAL ARTIFACTS (Docker approach):
- lambda_diagnostic_agent/ (Docker build directory)

LOCAL ARTIFACTS (ZIP approach):
- lambda_diagnostic_agent_zip/ (ZIP build directory with lib/ dependencies)
- lambda_diagnostic_agent_zip.zip (ZIP package file)
- Any other *_zip directories (catch-all pattern)
- Any other *.zip files (catch-all pattern)

TEMPORARY FILES:
- __pycache__/ directories
- *.pyc compiled Python files

RESOURCES PRESERVED:
- Lab-02-diagnostics-agent.ipynb (notebook file preserved)
- lab_helpers/ module (preserved for reuse)
"""

import boto3
import time
import shutil
import os
from lab_helpers.constants import PARAMETER_PATHS


def cleanup_lab_02(region_name="us-west-2", cleanup_s3=True):
    """
    Clean up all Lab 02 resources (Docker and ZIP deployments)

    This function removes all AWS resources and local artifacts created during Lab 02:

    AWS CLEANUP:
    1. AgentCore Gateway (and all targets)
    2. Lambda function (aiml301-diagnostic-agent)
    3. ECR repository (if using Docker approach)
    4. S3 bucket and all deployment packages (if cleanup_s3=True)
    5. IAM roles (Lambda execution role, Gateway service role)
    6. Parameter Store entries
    7. CloudWatch logs

    LOCAL CLEANUP:
    - lambda_diagnostic_agent/ (Docker build artifacts)
    - lambda_diagnostic_agent_zip/ (ZIP build directory with dependencies)
    - lambda_diagnostic_agent_zip.zip (ZIP package)
    - Any other *_zip directories and *.zip files (pattern-based)
    - Python cache (__pycache__/, *.pyc)

    Args:
        region_name: AWS region (default: us-west-2)
        cleanup_s3: Also clean up S3 bucket and objects (default: True)
                   Set to False if you want to preserve S3 deployment packages

    Returns:
        None (prints status to stdout)

    Example:
        from lab_helpers.lab_02.cleanup import cleanup_lab_02
        cleanup_lab_02(region_name="us-west-2", cleanup_s3=True)
    """
    print("🧹 Cleaning up Lab 02 resources...\n")
    print("=" * 70)

    # Initialize clients
    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region_name)
    lambda_client = boto3.client("lambda", region_name=region_name)
    ecr_client = boto3.client("ecr", region_name=region_name)
    s3_client = boto3.client("s3", region_name=region_name)
    iam_client = boto3.client("iam")
    ssm_client = boto3.client("ssm", region_name=region_name)
    logs_client = boto3.client("logs", region_name=region_name)

    # 1. Delete Gateway (targets first, then gateway)
    print("[1/7] Deleting Gateway and targets...")
    try:
        # Find gateway by name
        gateways = agentcore_client.list_gateways()
        for gw in gateways.get("items", []):
            if gw["name"] == "aiml301-diagnostics-gateway":
                gateway_id = gw["gatewayId"]
                targets_deleted = True  # Assume success unless proven otherwise

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
                                        f"  ⏳ Retry {retry_count}/{max_retries - 1}: {remaining_count} target(s) still present..."
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

    # 2. Delete Lambda function
    print("[2/7] Deleting Lambda function...")
    try:
        lambda_client.delete_function(FunctionName="aiml301-diagnostic-agent")
        print("  ✓ Lambda deleted")
    except lambda_client.exceptions.ResourceNotFoundException:
        print("  ✓ Lambda not found (ok)")
    except Exception as e:
        print(f"  ⚠ Lambda deletion: {e}")

    # 3. Delete ECR repository
    print("[3/7] Deleting ECR repository...")
    try:
        ecr_client.delete_repository(repositoryName="aiml301-diagnostic-agent", force=True)
        print("  ✓ ECR repository deleted")
    except ecr_client.exceptions.RepositoryNotFoundException:
        print("  ✓ ECR repository not found (ok)")
    except Exception as e:
        print(f"  ⚠ ECR deletion: {e}")

    # 3.5. Delete S3 deployment packages (ZIP-based deployment)
    if cleanup_s3:
        print("[3.5/7] Deleting S3 deployment packages...")
        try:
            bucket_name = "aiml301-lambda-packages"
            # List all objects in bucket
            try:
                response = s3_client.list_objects_v2(Bucket=bucket_name)
                if "Contents" in response:
                    for obj in response["Contents"]:
                        s3_client.delete_object(Bucket=bucket_name, Key=obj["Key"])
                        print(f"    • Deleted: {obj['Key']}")

                # Delete bucket itself
                s3_client.delete_bucket(Bucket=bucket_name)
                print(f"  ✓ S3 bucket deleted: {bucket_name}")
            except s3_client.exceptions.NoSuchBucket:
                print(f"  ✓ S3 bucket not found (ok): {bucket_name}")
        except Exception as e:
            print(f"  ⚠ S3 cleanup: {e}")

    # 4. Delete IAM roles
    print("[4/7] Deleting IAM roles...")

    # Delete Lambda execution role
    try:
        _delete_role(iam_client, "aiml301-diagnostic-lambda-role")
        print("  ✓ Lambda execution role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Lambda execution role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Lambda role: {e}")

    # Delete Gateway service role
    try:
        _delete_role(iam_client, "aiml301-gateway-service-role")
        print("  ✓ Gateway service role deleted")
    except iam_client.exceptions.NoSuchEntityException:
        print("  ✓ Gateway service role not found (ok)")
    except Exception as e:
        print(f"  ⚠ Gateway role: {e}")

    # 5. Delete Parameter Store entries (using constants for consistency)
    print("[5/7] Deleting Parameter Store entries...")
    try:
        params_to_delete = [
            PARAMETER_PATHS["lab_02"]["ecr_repository_uri"],
            PARAMETER_PATHS["lab_02"]["ecr_repository_name"],
            PARAMETER_PATHS["lab_02"]["lambda_role_arn"],
            PARAMETER_PATHS["lab_02"]["lambda_function_arn"],
            PARAMETER_PATHS["lab_02"]["gateway_role_arn"],
            PARAMETER_PATHS["lab_02"]["lambda_function_name"],
            PARAMETER_PATHS["lab_02"]["gateway_id"],
            PARAMETER_PATHS["lab_02"]["gateway_url"],
        ]
        # Filter out any None values
        params_to_delete = [p for p in params_to_delete if p]
        if params_to_delete:
            ssm_client.delete_parameters(Names=params_to_delete)
            print(f"  ✓ Parameter Store entries deleted ({len(params_to_delete)} parameters)")
        else:
            print("  ✓ No parameters to delete")
    except Exception as e:
        print(f"  ⚠ Parameters: {e}")

    # 6. Delete CloudWatch logs
    print("[6/7] Deleting CloudWatch log groups...")
    try:
        logs_client.delete_log_group(logGroupName="/aws/lambda/aiml301-diagnostic-agent")
        print("  ✓ Lambda log group deleted")
    except logs_client.exceptions.ResourceNotFoundException:
        print("  ✓ Lambda log group not found (ok)")
    except Exception as e:
        print(f"  ⚠ Log group: {e}")

    # 7. Delete build artifacts (both Docker and ZIP approaches)
    print("[7/7] Deleting build artifacts and temporary files...")
    try:
        import glob

        artifacts_deleted = 0

        # Docker build directory
        docker_dir = "lambda_diagnostic_agent"
        if os.path.exists(docker_dir):
            shutil.rmtree(docker_dir)
            print(f"  ✓ Docker build directory removed: {docker_dir}")
            artifacts_deleted += 1
        else:
            print("  ✓ Docker build directory not found (ok)")

        # ZIP build directory (specific)
        zip_build_dir = "lambda_diagnostic_agent_zip"
        if os.path.exists(zip_build_dir):
            shutil.rmtree(zip_build_dir)
            print(f"  ✓ ZIP build directory removed: {zip_build_dir}")
            artifacts_deleted += 1
        else:
            print("  ✓ ZIP build directory not found (ok)")

        # ZIP file (specific)
        zip_file = "lambda_diagnostic_agent_zip.zip"
        if os.path.exists(zip_file):
            os.remove(zip_file)
            print(f"  ✓ ZIP file removed: {zip_file}")
            artifacts_deleted += 1
        else:
            print("  ✓ ZIP file not found (ok)")

        # Clean up any other *_zip directories (catch-all for alternative patterns)
        zip_dirs = glob.glob("*_zip")
        for zip_dir in zip_dirs:
            if os.path.isdir(zip_dir) and zip_dir != zip_build_dir:
                try:
                    shutil.rmtree(zip_dir)
                    print(f"  ✓ Additional ZIP directory removed: {zip_dir}")
                    artifacts_deleted += 1
                except Exception as e:
                    print(f"  ⚠ Could not remove {zip_dir}: {e}")

        # Clean up any other *.zip files (catch-all for alternative patterns)
        zip_files = glob.glob("*.zip")
        for zf in zip_files:
            if zf != zip_file:
                try:
                    os.remove(zf)
                    print(f"  ✓ Additional ZIP file removed: {zf}")
                    artifacts_deleted += 1
                except Exception as e:
                    print(f"  ⚠ Could not remove {zf}: {e}")

        # Clean up __pycache__ directories that might have been created
        pycache_dirs = glob.glob("**/__pycache__", recursive=True)
        for cache_dir in pycache_dirs:
            try:
                shutil.rmtree(cache_dir)
                print(f"  ✓ Python cache removed: {cache_dir}")
                artifacts_deleted += 1
            except Exception:
                pass  # Silent fail for cache cleanup

        # Clean up *.pyc files
        pyc_files = glob.glob("**/*.pyc", recursive=True)
        for pyc in pyc_files:
            try:
                os.remove(pyc)
                artifacts_deleted += 1
            except Exception:
                pass  # Silent fail for pyc cleanup

        if artifacts_deleted > 0:
            print(f"\n  Total artifacts cleaned: {artifacts_deleted}")

    except Exception as e:
        print(f"  ⚠ Build artifacts cleanup: {e}")

    print("\n" + "=" * 70)
    print("✅ Lab 02 cleanup complete")
    print("\nYou can now re-run the entire Lab 02 from Section 1")


def _delete_role(iam_client, role_name):
    """Helper: Detach all policies and delete role"""
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

    cleanup_lab_02(region_name=AWS_REGION)
