#!/usr/bin/env python3
"""
Complete Market Trends Agent Cleanup Script
Removes all resources created by deploy.py including:
- AgentCore Runtime instances
- AgentCore Memory instances
- ECR repositories
- IAM roles and policies
- SSM parameters
- CodeBuild projects
- S3 artifacts
"""

import argparse
import logging
import boto3
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


class MarketTrendsAgentCleaner:
    """Complete cleaner for Market Trends Agent resources"""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self.agent_name = "market_trends_agent"
        self.role_name = "MarketTrendsAgentRole"

        # Initialize AWS clients
        self.iam_client = boto3.client("iam", region_name=region)
        self.ecr_client = boto3.client("ecr", region_name=region)
        self.ssm_client = boto3.client("ssm", region_name=region)
        self.codebuild_client = boto3.client("codebuild", region_name=region)
        self.s3_client = boto3.client("s3", region_name=region)

        try:
            from bedrock_agentcore.memory import MemoryClient

            self.memory_client = MemoryClient(region_name=region)
            self.agentcore_control = boto3.client("bedrock-agentcore-control", region_name=region)
            self.agentcore_available = True
        except ImportError:
            logger.warning("bedrock-agentcore SDK not available - skipping AgentCore cleanup")
            self.agentcore_available = False

    def cleanup_agentcore_runtime(self):
        """Remove AgentCore Runtime instances"""
        if not self.agentcore_available:
            logger.info("Skipping AgentCore Runtime cleanup (SDK not available)")
            return

        logger.info("🗑️  Cleaning up AgentCore Runtime instances...")

        try:
            # Check if .agent_arn file exists
            arn_file = Path(".agent_arn")
            if arn_file.exists():
                with open(arn_file, "r") as f:
                    agent_arn = f.read().strip()

                logger.info(f"   Found agent ARN: {agent_arn}")

                # Try to delete the runtime
                try:
                    # Extract agent ID from ARN
                    agent_id = agent_arn.split("/")[-1]
                    logger.info(f"   Deleting runtime: {agent_id}")

                    # Delete the runtime via bedrock-agentcore-control
                    self.agentcore_control.delete_agent_runtime(agentRuntimeId=agent_id)
                    logger.info("   ✅ AgentCore Runtime deleted successfully")

                    # Remove the ARN file
                    arn_file.unlink()
                    logger.info("   ✅ Removed .agent_arn file")

                except Exception as e:
                    logger.warning(f"   ⚠️  Could not delete runtime: {e}")
                    logger.info("   💡 Runtime may need manual cleanup in AWS Console")
            else:
                logger.info("   📋 No .agent_arn file found - no runtime to clean up")

        except Exception as e:
            logger.error(f"   ❌ Error during AgentCore Runtime cleanup: {e}")

    def cleanup_agentcore_memory(self):
        """Remove AgentCore Memory instances"""
        if not self.agentcore_available:
            logger.info("Skipping AgentCore Memory cleanup (SDK not available)")
            return

        logger.info("🗑️  Cleaning up AgentCore Memory instances...")

        try:
            memories = self.memory_client.list_memories()
            market_memories = [m for m in memories if m.get("id", "").startswith("MarketTrendsAgentMultiStrategy-")]

            if market_memories:
                logger.info(f"   Found {len(market_memories)} memory instances to delete")

                for memory in market_memories:
                    memory_id = memory.get("id")
                    status = memory.get("status")

                    try:
                        logger.info(f"   Deleting memory: {memory_id} (status: {status})")
                        self.memory_client.delete_memory(memory_id)
                        logger.info(f"   ✅ Deleted memory: {memory_id}")
                    except Exception as e:
                        logger.warning(f"   ⚠️  Could not delete memory {memory_id}: {e}")

                # Remove local memory ID file
                memory_id_file = Path(".memory_id")
                if memory_id_file.exists():
                    memory_id_file.unlink()
                    logger.info("   ✅ Removed .memory_id file")

            else:
                logger.info("   📋 No MarketTrendsAgent memory instances found")

        except Exception as e:
            logger.error(f"   ❌ Error during AgentCore Memory cleanup: {e}")

    def cleanup_ssm_parameters(self):
        """Remove SSM parameters"""
        logger.info("🗑️  Cleaning up SSM parameters...")

        param_name = "/bedrock-agentcore/market-trends-agent/memory-id"

        try:
            self.ssm_client.delete_parameter(Name=param_name)
            logger.info(f"   ✅ Deleted SSM parameter: {param_name}")
        except self.ssm_client.exceptions.ParameterNotFound:
            logger.info(f"   📋 SSM parameter not found: {param_name}")
        except Exception as e:
            logger.warning(f"   ⚠️  Could not delete SSM parameter: {e}")

    def cleanup_ecr_repository(self):
        """Remove ECR repository"""
        logger.info("🗑️  Cleaning up ECR repository...")

        repo_name = f"bedrock-agentcore-{self.agent_name}"

        try:
            # First, delete all images in the repository
            try:
                images = self.ecr_client.list_images(repositoryName=repo_name)
                if images["imageIds"]:
                    logger.info(f"   Deleting {len(images['imageIds'])} images from repository")
                    self.ecr_client.batch_delete_image(repositoryName=repo_name, imageIds=images["imageIds"])
                    logger.info("   ✅ Deleted all images from repository")
            except Exception as e:
                logger.warning(f"   ⚠️  Could not delete images: {e}")

            # Delete the repository
            self.ecr_client.delete_repository(repositoryName=repo_name, force=True)
            logger.info(f"   ✅ Deleted ECR repository: {repo_name}")

        except self.ecr_client.exceptions.RepositoryNotFoundException:
            logger.info(f"   📋 ECR repository not found: {repo_name}")
        except Exception as e:
            logger.warning(f"   ⚠️  Could not delete ECR repository: {e}")

    def cleanup_codebuild_project(self):
        """Remove CodeBuild project"""
        logger.info("🗑️  Cleaning up CodeBuild project...")

        project_name = f"bedrock-agentcore-{self.agent_name}-builder"

        try:
            self.codebuild_client.delete_project(name=project_name)
            logger.info(f"   ✅ Deleted CodeBuild project: {project_name}")
        except self.codebuild_client.exceptions.InvalidInputException:
            logger.info(f"   📋 CodeBuild project not found: {project_name}")
        except Exception as e:
            logger.warning(f"   ⚠️  Could not delete CodeBuild project: {e}")

    def cleanup_s3_artifacts(self):
        """Remove S3 artifacts (best effort)"""
        logger.info("🗑️  Cleaning up S3 artifacts...")

        try:
            # List buckets and look for CodeBuild artifacts
            buckets = self.s3_client.list_buckets()

            for bucket in buckets["Buckets"]:
                bucket_name = bucket["Name"]

                # Look for CodeBuild artifact buckets
                if "codebuild" in bucket_name.lower() and self.region in bucket_name:
                    try:
                        # List objects with our agent prefix
                        objects = self.s3_client.list_objects_v2(Bucket=bucket_name, Prefix=self.agent_name)

                        if "Contents" in objects:
                            logger.info(f"   Found {len(objects['Contents'])} artifacts in bucket: {bucket_name}")

                            # Delete objects
                            delete_objects = [{"Key": obj["Key"]} for obj in objects["Contents"]]
                            if delete_objects:
                                self.s3_client.delete_objects(
                                    Bucket=bucket_name,
                                    Delete={"Objects": delete_objects},
                                )
                                logger.info(f"   ✅ Deleted {len(delete_objects)} artifacts from {bucket_name}")

                    except Exception as e:
                        logger.debug(f"   Could not clean bucket {bucket_name}: {e}")

        except Exception as e:
            logger.warning(f"   ⚠️  Could not clean S3 artifacts: {e}")

    def cleanup_iam_resources(self):
        """Remove IAM roles and policies"""
        logger.info("🗑️  Cleaning up IAM resources...")

        # Clean up main execution role
        try:
            # Delete inline policies
            try:
                policies = self.iam_client.list_role_policies(RoleName=self.role_name)
                for policy_name in policies["PolicyNames"]:
                    self.iam_client.delete_role_policy(RoleName=self.role_name, PolicyName=policy_name)
                    logger.info(f"   ✅ Deleted inline policy: {policy_name}")
            except Exception as e:
                logger.debug(f"   Could not delete inline policies: {e}")

            # Delete the role
            self.iam_client.delete_role(RoleName=self.role_name)
            logger.info(f"   ✅ Deleted IAM role: {self.role_name}")

        except self.iam_client.exceptions.NoSuchEntityException:
            logger.info(f"   📋 IAM role not found: {self.role_name}")
        except Exception as e:
            logger.warning(f"   ⚠️  Could not delete IAM role: {e}")

        # Clean up CodeBuild execution role
        codebuild_role_pattern = f"AmazonBedrockAgentCoreSDKCodeBuild-{self.region}-"

        try:
            roles = self.iam_client.list_roles()
            for role in roles["Roles"]:
                role_name = role["RoleName"]
                if role_name.startswith(codebuild_role_pattern):
                    try:
                        # Delete inline policies
                        policies = self.iam_client.list_role_policies(RoleName=role_name)
                        for policy_name in policies["PolicyNames"]:
                            self.iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

                        # Delete attached managed policies
                        attached_policies = self.iam_client.list_attached_role_policies(RoleName=role_name)
                        for policy in attached_policies["AttachedPolicies"]:
                            self.iam_client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

                        # Delete the role
                        self.iam_client.delete_role(RoleName=role_name)
                        logger.info(f"   ✅ Deleted CodeBuild IAM role: {role_name}")

                    except Exception as e:
                        logger.warning(f"   ⚠️  Could not delete CodeBuild role {role_name}: {e}")

        except Exception as e:
            logger.warning(f"   ⚠️  Could not clean CodeBuild IAM roles: {e}")

    def cleanup_local_files(self):
        """Remove local deployment files"""
        logger.info("🗑️  Cleaning up local files...")

        files_to_remove = [
            ".agent_arn",
            ".memory_id",
            "Dockerfile",
            ".dockerignore",
            ".bedrock_agentcore.yaml",
        ]

        for file_name in files_to_remove:
            file_path = Path(file_name)
            if file_path.exists():
                file_path.unlink()
                logger.info(f"   ✅ Removed: {file_name}")
            else:
                logger.debug(f"   📋 File not found: {file_name}")

    def cleanup_all(self, skip_iam: bool = False):
        """Clean up all resources"""
        logger.info("🧹 Starting complete Market Trends Agent cleanup...")
        logger.info(f"   📍 Region: {self.region}")
        logger.info(f"   🎯 Agent: {self.agent_name}")

        # Clean up in reverse order of creation
        self.cleanup_agentcore_runtime()
        self.cleanup_agentcore_memory()
        self.cleanup_ssm_parameters()
        self.cleanup_codebuild_project()
        self.cleanup_s3_artifacts()
        self.cleanup_ecr_repository()

        if not skip_iam:
            self.cleanup_iam_resources()
        else:
            logger.info("🔄 Skipping IAM cleanup (--skip-iam flag)")

        self.cleanup_local_files()

        logger.info("✅ Cleanup completed!")
        logger.info("💡 If any resources couldn't be deleted, check the AWS Console manually")


def main():
    """Main cleanup function"""
    parser = argparse.ArgumentParser(description="Clean up all Market Trends Agent resources")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument(
        "--skip-iam",
        action="store_true",
        help="Skip IAM role cleanup (useful if roles are shared)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )

    args = parser.parse_args()

    if args.dry_run:
        logger.info("🔍 DRY RUN MODE - No resources will be deleted")
        logger.info("   This would clean up:")
        logger.info("   - AgentCore Runtime instances")
        logger.info("   - AgentCore Memory instances")
        logger.info("   - ECR repositories")
        logger.info("   - CodeBuild projects")
        logger.info("   - S3 artifacts")
        logger.info("   - SSM parameters")
        if not args.skip_iam:
            logger.info("   - IAM roles and policies")
        logger.info("   - Local deployment files")
        return

    # Confirm deletion
    print("⚠️  WARNING: This will delete ALL Market Trends Agent resources!")
    print(f"   Region: {args.region}")
    if args.skip_iam:
        print("   IAM resources will be PRESERVED")
    else:
        print("   IAM resources will be DELETED")

    confirm = input("\nAre you sure you want to continue? (type 'yes' to confirm): ")
    if confirm.lower() != "yes":
        print("❌ Cleanup cancelled")
        return

    # Create cleaner and run cleanup
    cleaner = MarketTrendsAgentCleaner(region=args.region)
    cleaner.cleanup_all(skip_iam=args.skip_iam)


if __name__ == "__main__":
    main()
