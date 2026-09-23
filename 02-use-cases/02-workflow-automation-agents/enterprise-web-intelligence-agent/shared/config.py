"""Configuration for the Competitive Intelligence Agent."""

import os
import boto3
from dataclasses import dataclass


@dataclass
class AgentConfig:
    """Configuration settings for the agent."""

    # AWS Configuration
    region: str = os.environ.get("AWS_REGION", "us-west-2")

    llm_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

    # Get AWS Account ID automatically
    @property
    def aws_account_id(self) -> str:
        if not hasattr(self, "_account_id"):
            try:
                sts = boto3.client("sts")
                self._account_id = sts.get_caller_identity()["Account"]
            except Exception:
                self._account_id = os.environ.get("AWS_ACCOUNT_ID", "unknown")
        return self._account_id

    # S3 Configuration for recordings - use property to get dynamic account ID
    @property
    def s3_bucket(self) -> str:
        return os.environ.get("S3_RECORDING_BUCKET", f"bedrock-agentcore-recordings-{self.aws_account_id}")

    s3_prefix: str = os.environ.get("S3_RECORDING_PREFIX", "competitive_intel/")

    # IAM Role for recording - use property for dynamic account ID
    @property
    def recording_role_arn(self) -> str:
        role_arn = os.environ.get("RECORDING_ROLE_ARN", "")
        if not role_arn and self.aws_account_id != "unknown":
            role_arn = f"arn:aws:iam::{self.aws_account_id}:role/BedrockAgentCoreRole"
        return role_arn

    # Browser Configuration
    browser_timeout: int = 60000  # 60 seconds
    browser_session_timeout: int = 3600  # 1 hour

    # Code Interpreter Configuration
    code_session_timeout: int = 1800  # 30 minutes

    # Live View Configuration
    live_view_port: int = 8000
    replay_viewer_port: int = 8002

    def validate(self) -> bool:
        """Validate required configuration."""
        if not self.recording_role_arn:
            print("WARNING: RECORDING_ROLE_ARN not set")
            print(f"AWS Account ID: {self.aws_account_id}")
            return False

        # Create the S3 bucket if it doesn't exist
        try:
            s3 = boto3.client("s3", region_name=self.region)
            try:
                s3.head_bucket(Bucket=self.s3_bucket)
                print(f"✅ S3 bucket exists: {self.s3_bucket}")
            except Exception:
                # Try to create the bucket
                if self.region == "us-east-1":
                    s3.create_bucket(Bucket=self.s3_bucket)
                else:
                    s3.create_bucket(
                        Bucket=self.s3_bucket,
                        CreateBucketConfiguration={"LocationConstraint": self.region},
                    )
                print(f"✅ Created S3 bucket: {self.s3_bucket}")
        except Exception as e:
            print(f"⚠️ Could not verify/create S3 bucket: {e}")
            print("You may need to create it manually or adjust permissions")

        return True
