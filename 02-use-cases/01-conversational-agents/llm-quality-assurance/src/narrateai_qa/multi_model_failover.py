"""Cross-account multi-model failover for Amazon Bedrock.

Bedrock rate limits are independent per model and per account, so N models
x M accounts gives N*M separate quota spaces. MultiModelBedrockModel walks
that grid on throttle: models in rank order (best quality first), account
order shuffled per request, immediate retry instead of backoff.
"""

import logging
import os
import random
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError
from strands.models import BedrockModel
from strands.types.content import Messages, SystemContentBlock
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """One ranked model and the IAM roles (one per account) that can reach it.

    Bedrock quotas are independent per model and per account, so each
    (model, role) pair is its own quota space.
    """

    rank: int
    model_id: str
    region: str
    role_arn_list: list[str]

    @classmethod
    def from_dict(cls, rank: int, config: dict) -> "ModelConfig":
        return cls(
            rank=rank,
            model_id=config["model_id"],
            region=config["region"],
            role_arn_list=config["client_kwargs"]["bedrock_role_arn_list"],
        )

    @classmethod
    def parse_configs(cls, model_configs: dict) -> list["ModelConfig"]:
        """Parse a {rank: config} dict into a rank-ordered list."""
        return [cls.from_dict(rank, config) for rank, config in sorted(model_configs.items(), key=lambda x: x[0])]


class CredentialsManager:
    """Assumes IAM roles to reach Bedrock in other accounts."""

    def assume_role(self, role_arn: str) -> dict:
        """Assume the given role. A fresh STS client per call avoids
        ExpiredToken errors from cached credentials."""
        sts_client = boto3.client("sts")
        session_name = f"bedrock-session-{uuid.uuid4()}"
        return sts_client.assume_role(RoleArn=role_arn, RoleSessionName=session_name)

    def get_environment_role_arns(self) -> list[str] | None:
        """Optional BEDROCK_ROLE_ARN override (comma-separated for multiple
        accounts); useful for local testing."""
        raw = os.environ.get("BEDROCK_ROLE_ARN", "")
        arns = [a.strip() for a in raw.split(",") if a.strip()]
        return arns or None


class ThrottlingDetector:
    """Throttling errors trigger failover to the next quota space;
    all other errors are real failures and should propagate."""

    THROTTLING_ERROR_CODES = {
        "ThrottlingException",
        "ServiceQuotaExceededException",
        "TooManyRequestsException",
    }

    @classmethod
    def is_throttling_error(cls, error: ClientError) -> bool:
        error_code = error.response.get("Error", {}).get("Code", "")
        return error_code in cls.THROTTLING_ERROR_CODES


class MultiModelBedrockModel(BedrockModel):
    """Drop-in BedrockModel replacement that treats every (model, account)
    pair as an independent quota space.

    Bedrock rate limits are per model per account, so N models x M accounts
    gives N*M separate quotas. On a throttle, the stream retries immediately
    against the next pair instead of backing off against the same exhausted
    quota: models are tried in rank order (best quality first), and account
    order is shuffled per request to prevent hot spotting.

    model_configs format:
        {
            1: {"model_id": "...", "region": "us-west-2",
                "client_kwargs": {"bedrock_role_arn_list": ["arn:aws:iam::...", ...]}},
            2: {...},
        }
    """

    def __init__(
        self,
        model_configs: dict,
        boto_client_config: Any | None = None,
        endpoint_url: str | None = None,
        **bedrock_config: Any,
    ):
        if not model_configs:
            raise ValueError("model_configs cannot be empty")

        self.models = ModelConfig.parse_configs(model_configs)
        self.credentials_manager = CredentialsManager()

        primary_model = self.models[0]

        # BEDROCK_ROLE_ARN env var overrides the configured role list
        env_role_arns = self.credentials_manager.get_environment_role_arns()
        if env_role_arns:
            self.role_arn_list = env_role_arns
        else:
            self.role_arn_list = primary_model.role_arn_list.copy()

        # Initial session on the primary model's first role
        assumed_role = self.credentials_manager.assume_role(self.role_arn_list[0])
        credentials = assumed_role["Credentials"]
        initial_session = boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials.get("SessionToken"),
            region_name=primary_model.region,
        )

        super().__init__(
            model_id=primary_model.model_id,
            boto_session=initial_session,
            boto_client_config=boto_client_config,
            endpoint_url=endpoint_url,
            **bedrock_config,
        )

        self.region_name = primary_model.region
        self.boto_client_config = boto_client_config
        self.endpoint_url = endpoint_url

        logger.info(f"MultiModelBedrockModel ready: {len(self.models)} models x {len(self.role_arn_list)} accounts")

    def _create_session_for_role(self, role_arn: str) -> boto3.Session:
        assumed_role = self.credentials_manager.assume_role(role_arn)
        credentials = assumed_role["Credentials"]
        return boto3.Session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials.get("SessionToken"),
            region_name=self.region_name,
        )

    def _swap_to_model_and_account(self, model_id: str, role_arn: str) -> None:
        """Rebuild the Bedrock client on a new assumed-role session."""
        new_session = self._create_session_for_role(role_arn)
        self._boto_session = new_session
        self._client = new_session.client(
            "bedrock-runtime",
            region_name=self.region_name,
            endpoint_url=self.endpoint_url,
            config=self.boto_client_config,
        )
        self.config["model_id"] = model_id

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream with failover: walk the (model, account) grid until one
        pair succeeds or all are exhausted."""
        start_time = time.time()
        last_error: Exception | None = None

        for model in self.models:
            logger.info(f"Attempting model: {model.model_id}")

            # Shuffle is for load distribution across accounts, not crypto
            shuffled_roles = self.role_arn_list.copy()
            random.shuffle(shuffled_roles)  # nosec B311

            for role_arn in shuffled_roles:
                try:
                    self._swap_to_model_and_account(model.model_id, role_arn)

                    async for event in super().stream(
                        messages,
                        tool_specs,
                        system_prompt,
                        tool_choice=tool_choice,
                        system_prompt_content=system_prompt_content,
                        **kwargs,
                    ):
                        yield event

                    duration = time.time() - start_time
                    logger.info(
                        f"Completed with model {model.model_id} using role {role_arn[:20]}... in {duration:.2f}s"
                    )
                    return

                except (ClientError, Exception) as e:
                    is_throttling = (
                        isinstance(e, ClientError) and ThrottlingDetector.is_throttling_error(e)
                    ) or "ThrottlingException" in str(e)

                    if not is_throttling:
                        # Real failure, don't mask it with a failover
                        logger.error(f"Non-throttling error with model {model.model_id}: {e}")
                        raise

                    logger.warning(
                        f"Throttled on model {model.model_id} with role {role_arn[:20]}..., trying next quota space"
                    )
                    last_error = e
                    continue

        raise Exception(f"All models and roles exhausted due to throttling. Last error: {last_error}") from last_error
