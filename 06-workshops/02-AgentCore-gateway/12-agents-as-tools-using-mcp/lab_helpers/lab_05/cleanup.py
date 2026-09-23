"""
Lab 05: Cleanup Supervisor Agent Resources

Cleans up all resources created during Lab 05 deployment:
- Supervisor Agent Runtime
- IAM Role
- ECR Repository (optional)
- agent-supervisor.py file
- Dockerfile
- .bedrock_agentcore.yaml
"""

import os
import boto3
import logging
from typing import Dict, List
from botocore.exceptions import ClientError

from lab_helpers.config import AWS_REGION
from .iam_setup import delete_supervisor_runtime_iam_role

logger = logging.getLogger(__name__)


def delete_supervisor_runtime(runtime_name: str, region: str = AWS_REGION, verbose: bool = True) -> bool:
    """
    Delete supervisor agent runtime.

    Args:
        runtime_name: Name of the supervisor runtime to delete
        region: AWS region
        verbose: Print status messages

    Returns:
        True if successful, False otherwise
    """
    try:
        agentcore = boto3.client("bedrock-agentcore-control", region_name=region)

        if verbose:
            logger.info(f"🗑️  Deleting supervisor runtime: {runtime_name}")

        # List runtimes to find the one to delete
        response = agentcore.list_agent_runtimes()
        runtime_id = None

        for runtime in response.get("agentRuntimes", []):
            if runtime["agentRuntimeName"] == runtime_name:
                runtime_id = runtime["agentRuntimeId"]
                break

        if not runtime_id:
            if verbose:
                logger.warning(f"⚠️  Runtime not found: {runtime_name}")
            return True

        # Delete the runtime
        agentcore.delete_agent_runtime(agentRuntimeId=runtime_id)

        if verbose:
            logger.info(f"✅ Supervisor runtime deleted: {runtime_id}")

        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            if verbose:
                logger.warning(f"⚠️  Runtime not found: {runtime_name}")
            return True
        logger.error(f"❌ Error deleting runtime: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error deleting runtime: {e}")
        return False


def delete_supervisor_gateway(gateway_name: str, region: str = AWS_REGION, verbose: bool = True) -> bool:
    """
    Delete supervisor gateway.

    Args:
        gateway_name: Name of the supervisor gateway to delete
        region: AWS region
        verbose: Print status messages

    Returns:
        True if successful, False otherwise
    """
    try:
        agentcore = boto3.client("bedrock-agentcore-control", region_name=region)

        if verbose:
            logger.info(f"🗑️  Deleting supervisor gateway: {gateway_name}")

        # List gateways to find the one to delete
        response = agentcore.list_gateways()
        gateway_id = None

        for gateway in response.get("gatewaySummaries", []):
            if gateway_name in gateway["gatewayArn"]:
                gateway_id = gateway["gatewayId"]
                break

        if not gateway_id:
            if verbose:
                logger.warning(f"⚠️  Gateway not found: {gateway_name}")
            return True

        # Delete the gateway
        agentcore.delete_gateway(gatewayIdentifier=gateway_id)

        if verbose:
            logger.info(f"✅ Supervisor gateway deleted: {gateway_id}")

        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            if verbose:
                logger.warning(f"⚠️  Gateway not found: {gateway_name}")
            return True
        logger.error(f"❌ Error deleting gateway: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error deleting gateway: {e}")
        return False


def delete_ecr_repository(
    repository_name: str,
    region: str = AWS_REGION,
    verbose: bool = True,
    force: bool = True,
) -> bool:
    """
    Delete ECR repository for supervisor runtime.

    Args:
        repository_name: Name of the ECR repository
        region: AWS region
        verbose: Print status messages
        force: Force delete even if repository has images

    Returns:
        True if successful, False otherwise
    """
    try:
        ecr = boto3.client("ecr", region_name=region)

        if verbose:
            logger.info(f"🗑️  Deleting ECR repository: {repository_name}")

        ecr.delete_repository(repositoryName=repository_name, force=force)

        if verbose:
            logger.info(f"✅ ECR repository deleted: {repository_name}")

        return True

    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            if verbose:
                logger.warning(f"⚠️  Repository not found: {repository_name}")
            return True
        logger.error(f"❌ Error deleting ECR repository: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error deleting ECR repository: {e}")
        return False


def delete_supervisor_files(file_names: List[str] = None, verbose: bool = True) -> Dict[str, bool]:
    """
    Delete supervisor-related files from project root.

    Args:
        file_names: List of file names to delete (auto-defaults to standard files if not provided)
        verbose: Print status messages

    Returns:
        Dict with deletion status for each file
    """
    if file_names is None:
        file_names = ["agent-supervisor.py", "Dockerfile", ".bedrock_agentcore.yaml"]

    # Get the project root directory (3 levels up from lab_helpers/lab_05/cleanup.py)
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    deletion_status = {}

    for file_name in file_names:
        try:
            file_path = os.path.join(project_root, file_name)

            if verbose:
                logger.info(f"🗑️  Deleting {file_name}: {file_path}")

            if os.path.exists(file_path):
                os.remove(file_path)
                if verbose:
                    logger.info(f"✅ {file_name} deleted")
                deletion_status[file_name] = True
            else:
                if verbose:
                    logger.warning(f"⚠️  File not found: {file_path}")
                deletion_status[file_name] = True

        except Exception as e:
            logger.error(f"❌ Error deleting {file_name}: {e}")
            deletion_status[file_name] = False

    return deletion_status


def cleanup_lab_05(region_name: str = AWS_REGION, verbose: bool = True, delete_ecr: bool = True) -> Dict[str, bool]:
    """
    Clean up all Lab 05 resources.

    Args:
        region_name: AWS region
        verbose: Print status messages
        delete_ecr: Whether to delete ECR repository (default: True)

    Returns:
        Dict with cleanup status for each resource
    """
    logger.info("\n🧹 Starting Lab-05 Cleanup...")
    if verbose:
        logger.info("=" * 70)

    cleanup_status = {}

    # 1. Delete supervisor runtime
    if verbose:
        logger.info("\n1️⃣  Deleting Supervisor Runtime...")
    cleanup_status["runtime"] = delete_supervisor_runtime(
        runtime_name="aiml301_sre_agentcore_supervisor_runtime",
        region=region_name,
        verbose=verbose,
    )

    # 2. Delete IAM role
    if verbose:
        logger.info("\n2️⃣  Deleting IAM Role...")
    cleanup_status["iam_role"] = delete_supervisor_runtime_iam_role(
        role_name="aiml301_sre_agentcore-lab05-supervisor-runtime-role",
        region=region_name,
    )

    # 3. Delete ECR repository
    if verbose:
        logger.info("\n3️⃣  Deleting ECR Repository...")
    cleanup_status["ecr"] = delete_ecr_repository(
        repository_name="bedrock-agentcore-aiml301_sre_agentcore_supervisor_runtime",
        region=region_name,
        verbose=verbose,
        force=True,
    )

    # 4. Delete supervisor-related files
    if verbose:
        logger.info("\n4️⃣  Deleting Supervisor Files...")
    files_cleanup = delete_supervisor_files(verbose=verbose)
    cleanup_status.update(files_cleanup)

    # Summary
    if verbose:
        logger.info("\n" + "=" * 70)
        logger.info("✅ Lab-05 Cleanup Summary:")
        for resource, status in cleanup_status.items():
            status_icon = "✓" if status else "✗"
            logger.info(f"   {status_icon} {resource.upper()}: {'SUCCESS' if status else 'FAILED'}")

        logger.info("\n💡 All Lab-05 supervisor resources have been cleaned up!")
        logger.info("=" * 70)

    return cleanup_status
