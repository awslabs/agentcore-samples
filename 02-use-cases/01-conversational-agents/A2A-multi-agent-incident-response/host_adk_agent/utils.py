import boto3
from boto3.session import Session
import sys


def get_ssm_parameter(name: str, with_decryption: bool = True) -> str:
    """Get parameter from AWS Systems Manager Parameter Store."""
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=name, WithDecryption=with_decryption)
    return response["Parameter"]["Value"]


def get_aws_info():
    """Get AWS account ID and region from boto3 session."""
    try:
        boto_session = Session()

        # Get region
        region = boto_session.region_name
        if not region:
            # Try to get from default session
            region = boto3.DEFAULT_SESSION.region_name if boto3.DEFAULT_SESSION else None
            if not region:
                raise ValueError("AWS region not configured. Please set AWS_DEFAULT_REGION or configure AWS CLI.")

        # Get account ID using STS
        sts = boto_session.client("sts")
        account_id = sts.get_caller_identity()["Account"]

        return account_id, region

    except Exception as e:
        print(f"Error getting AWS info: {e}")
        print("Please ensure AWS credentials are configured (aws configure or environment variables)")
        sys.exit(1)
