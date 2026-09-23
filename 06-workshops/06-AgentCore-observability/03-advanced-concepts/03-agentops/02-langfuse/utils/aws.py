"""
Utility functions for AWS services and other common operations.
"""

import boto3
import json
from typing import Optional, Dict, Any
from botocore.exceptions import ClientError, NoCredentialsError


def get_ssm_parameter(
    parameter_name: str,
    decrypt: bool = True,
    region_name: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieve a parameter value from AWS Systems Manager Parameter Store.

    Args:
        parameter_name (str): The name of the parameter to retrieve
        decrypt (bool): Whether to decrypt SecureString parameters (default: True)
        region_name (str, optional): AWS region name
        aws_access_key_id (str, optional): AWS access key ID
        aws_secret_access_key (str, optional): AWS secret access key
        aws_session_token (str, optional): AWS session token

    Returns:
        str: The parameter value, or None if not found or error occurs

    Raises:
        NoCredentialsError: If AWS credentials are not configured
        ClientError: If there's an AWS service error
    """
    try:
        # Create SSM client with optional credentials
        session_kwargs = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        if aws_access_key_id:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            session_kwargs["aws_session_token"] = aws_session_token

        ssm_client = boto3.client("ssm", **session_kwargs)

        # Get parameter
        response = ssm_client.get_parameter(Name=parameter_name, WithDecryption=decrypt)

        return response["Parameter"]["Value"]

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ParameterNotFound":
            print(f"Parameter '{parameter_name}' not found")
            return None
        else:
            print(f"AWS error retrieving parameter '{parameter_name}': {e}")
            raise
    except NoCredentialsError:
        print("AWS credentials not found. Please configure your credentials.")
        raise
    except Exception as e:
        print(f"Unexpected error retrieving parameter '{parameter_name}': {e}")
        return None


def get_ssm_parameters_by_path(
    parameter_path: str,
    recursive: bool = True,
    decrypt: bool = True,
    region_name: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
) -> Dict[str, str]:
    """
    Retrieve multiple parameters from AWS Systems Manager Parameter Store by path.

    Args:
        parameter_path (str): The path prefix for parameters to retrieve
        recursive (bool): Whether to retrieve parameters recursively (default: True)
        decrypt (bool): Whether to decrypt SecureString parameters (default: True)
        region_name (str, optional): AWS region name
        aws_access_key_id (str, optional): AWS access key ID
        aws_secret_access_key (str, optional): AWS secret access key
        aws_session_token (str, optional): AWS session token

    Returns:
        Dict[str, str]: Dictionary mapping parameter names to their values

    Raises:
        NoCredentialsError: If AWS credentials are not configured
        ClientError: If there's an AWS service error
    """
    try:
        # Create SSM client with optional credentials
        session_kwargs = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        if aws_access_key_id:
            session_kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            session_kwargs["aws_session_token"] = aws_session_token

        ssm_client = boto3.client("ssm", **session_kwargs)

        parameters = {}
        paginator = ssm_client.get_paginator("get_parameters_by_path")

        # Paginate through all parameters
        for page in paginator.paginate(Path=parameter_path, Recursive=recursive, WithDecryption=decrypt):
            for param in page["Parameters"]:
                parameters[param["Name"]] = param["Value"]

        return parameters

    except ClientError as e:
        print(f"AWS error retrieving parameters from path '{parameter_path}': {e}")
        raise
    except NoCredentialsError:
        print("AWS credentials not found. Please configure your credentials.")
        raise
    except Exception as e:
        print(f"Unexpected error retrieving parameters from path '{parameter_path}': {e}")
        return {}


def get_ssm_parameter_as_json(
    parameter_name: str,
    decrypt: bool = True,
    region_name: Optional[str] = None,
    aws_access_key_id: Optional[str] = None,
    aws_secret_access_key: Optional[str] = None,
    aws_session_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Retrieve a parameter value from AWS Systems Manager Parameter Store and parse it as JSON.

    Args:
        parameter_name (str): The name of the parameter to retrieve
        decrypt (bool): Whether to decrypt SecureString parameters (default: True)
        region_name (str, optional): AWS region name
        aws_access_key_id (str, optional): AWS access key ID
        aws_secret_access_key (str, optional): AWS secret access key
        aws_session_token (str, optional): AWS session token

    Returns:
        Dict[str, Any]: The parsed JSON value, or None if not found or error occurs

    Raises:
        NoCredentialsError: If AWS credentials are not configured
        ClientError: If there's an AWS service error
        json.JSONDecodeError: If the parameter value is not valid JSON
    """
    try:
        parameter_value = get_ssm_parameter(
            parameter_name=parameter_name,
            decrypt=decrypt,
            region_name=region_name,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )

        if parameter_value is None:
            return None

        return json.loads(parameter_value)

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from parameter '{parameter_name}': {e}")
        return None
    except Exception as e:
        print(f"Unexpected error retrieving JSON parameter '{parameter_name}': {e}")
        return None


# Example usage:
if __name__ == "__main__":
    # Example 1: Get a single parameter
    api_key = get_ssm_parameter("/myapp/api-key")

    # Example 2: Get multiple parameters by path
    config_params = get_ssm_parameters_by_path("/myapp/config/")

    # Example 3: Get a JSON parameter
    db_config = get_ssm_parameter_as_json("/myapp/database-config")
