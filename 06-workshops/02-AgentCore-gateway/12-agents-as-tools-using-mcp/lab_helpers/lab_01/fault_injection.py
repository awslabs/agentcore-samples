"""
Fault injection functions for Lab 01
Implements three common infrastructure faults for SRE training
"""

import boto3
import json
import time
from typing import Dict
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor, as_completed
from .ssm_helper import get_stack_resources

# Global storage for original configurations (for potential future rollback)
original_configs = {}


def initialize_fault_injection(region_name: str, profile_name: str = None) -> Dict[str, str]:
    """
    Initialize fault injection by retrieving infrastructure resource IDs

    Args:
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Dictionary of resource identifiers
    """
    print("Retrieving infrastructure resources from SSM Parameter Store...")
    resources = get_stack_resources(region_name, profile_name)

    if len(resources) > 0:
        print(f"✅ Successfully retrieved {len(resources)} resource identifiers")
    else:
        print("❌ No resources retrieved - CloudFormation stack may not be deployed")

    return resources


def _update_single_table(dynamodb, table_name: str) -> tuple:
    """
    Update a single DynamoDB table to PROVISIONED mode with low capacity.
    Designed for parallel execution.

    Returns:
        tuple: (table_name, success, original_billing_mode_or_error)
    """
    try:
        # Store original billing mode for potential rollback
        print(f"Processing table: {table_name}")
        table_info = dynamodb.describe_table(TableName=table_name)
        original_billing_mode = table_info["Table"]["BillingModeSummary"]["BillingMode"]
        print(f"  Original billing mode: {original_billing_mode}")  # codeql[py/clear-text-logging-sensitive-data]

        # Convert to provisioned capacity with dangerously low limits
        print("  Converting to PROVISIONED mode with minimal capacity...")
        dynamodb.update_table(
            TableName=table_name,
            BillingMode="PROVISIONED",
            ProvisionedThroughput={
                "ReadCapacityUnits": 1,  # Extremely low - guaranteed to throttle
                "WriteCapacityUnits": 1,  # Extremely low - guaranteed to throttle
            },
        )

        # Wait for table update to complete
        print(f"  Waiting for {table_name} update to complete...")
        waiter = dynamodb.get_waiter("table_exists")
        waiter.wait(
            TableName=table_name,
            WaiterConfig={
                "Delay": 2,  # Check every 2 seconds (reduced from 5)
                "MaxAttempts": 90,  # 3 minutes max
            },
        )

        print(f"✅ Successfully updated {table_name}")
        return (table_name, True, original_billing_mode)

    except Exception as table_error:
        print(f"❌ Failed to update {table_name}: {table_error}")
        return (table_name, False, str(table_error))


def inject_dynamodb_throttling(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Inject DynamoDB throttling by converting tables to PROVISIONED mode with low capacity.
    This simulates a common production issue where table capacity is insufficient for
    the application workload, causing ProvisionedThroughputExceededException errors.

    Args:
        resources: Dictionary of resource identifiers from get_stack_resources()
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        # Get list of DynamoDB table names from resources
        table_keys = [key for key in resources.keys() if key.endswith("_table_name") and "crm" in key]

        if not table_keys:
            print("❌ No DynamoDB table names found in resources")
            return False

        # Create DynamoDB client
        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            dynamodb = session.client("dynamodb")
        else:
            dynamodb = boto3.client("dynamodb", region_name=region_name)

        print(f"\nFound {len(table_keys)} DynamoDB table(s) to modify")
        print("Processing tables in parallel for faster execution...")
        print(f"\n{'=' * 60}")

        success_count = 0
        failed_tables = []

        # Extract table names
        table_names = [resources.get(key) for key in table_keys if resources.get(key)]

        if not table_names:
            print("❌ No valid table names found")
            return False

        # Process tables concurrently
        max_workers = min(len(table_names), 10)  # Limit to 10 concurrent operations

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all table updates
            future_to_table = {
                executor.submit(_update_single_table, dynamodb, table_name): table_name for table_name in table_names
            }

            # Collect results as they complete
            for future in as_completed(future_to_table):
                table_name, success, result = future.result()

                if success:
                    # Store original config for rollback
                    original_configs[f"dynamodb_billing_mode_{table_name}"] = result
                    success_count += 1
                else:
                    failed_tables.append(table_name)

        # Summary
        print(f"\n{'=' * 60}")
        print(f"Summary: {success_count}/{len(table_names)} tables updated successfully")
        if failed_tables:
            print(f"Failed tables: {', '.join(failed_tables)}")
        print(f"{'=' * 60}")

        return success_count > 0

    except Exception as e:
        print(f"❌ DynamoDB throttling injection failed: {e}")
        return False


def inject_iam_permissions(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Inject IAM permission issues by replacing DynamoDB Allow policy with Deny policy

    This simulates a common production issue where overly restrictive security policies
    or accidental policy changes prevent applications from accessing required AWS resources.

    Args:
        resources: Dictionary of resource identifiers from get_stack_resources()
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        ec2_role_name = resources.get("ec2_role_name")

        if not ec2_role_name:
            print("❌ EC2 role name not found in resources")
            return False

        # Create IAM client
        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            iam = session.client("iam")
        else:
            iam = boto3.client("iam", region_name=region_name)

        print(f"\nTarget IAM role: {ec2_role_name}")

        # Store original policy for potential rollback
        print("Backing up original DynamoDB policy...")
        try:
            original_policy = iam.get_role_policy(RoleName=ec2_role_name, PolicyName="DynamoDBAccess")
            original_configs["dynamodb_policy"] = original_policy["PolicyDocument"]
            print("  ✅ Original policy backed up (redacted)")
        except ClientError:
            print("  ⚠️  Could not backup original policy (may not exist)")

        # Create a restrictive policy that denies DynamoDB access
        print("\nApplying restrictive IAM policy...")
        print("  Technical details:")
        print("  - Replacing existing 'Allow' statements with 'Deny' statements")
        print("  - Targeting key DynamoDB operations used by the application")
        print("  - Deny policies override any Allow policies (explicit deny wins)")
        print("  - Will cause immediate AccessDenied errors for database operations")

        restricted_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Deny",
                    "Action": [
                        "dynamodb:PutItem",
                        "dynamodb:GetItem",
                        "dynamodb:Query",
                        "dynamodb:Scan",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                    ],
                    "Resource": "*",
                }
            ],
        }

        iam.put_role_policy(
            RoleName=ec2_role_name,
            PolicyName="DynamoDBAccess",
            PolicyDocument=json.dumps(restricted_policy),
        )

        return True

    except Exception as e:
        print(f"❌ IAM permission injection failed: {e}")
        return False


def inject_nginx_crash(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Inject nginx crash by killing the nginx process via AWS Systems Manager

    This simulates a common production issue where services crash due to memory leaks,
    segmentation faults, or resource exhaustion, causing ALB health check failures.

    Args:
        resources: Dictionary of resource identifiers from get_stack_resources()
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        nginx_instance_id = resources.get("nginx_instance_id")

        if not nginx_instance_id:
            print("❌ Nginx instance ID not found in resources")
            return False

        # Create SSM client
        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            ssm = session.client("ssm")
        else:
            ssm = boto3.client("ssm", region_name=region_name)

        print(f"\nTarget EC2 instance: {nginx_instance_id}")
        print("\nSimulating service crash by killing nginx process...")
        print("  Technical details:")
        print("  - Using 'pkill -9 nginx' to forcefully terminate nginx processes")
        print("  - This simulates common production crashes (memory leaks, segfaults, etc.)")
        print("  - ALB health checks will get 'connection refused' when trying to reach /health")
        print("  - After 3 consecutive failures (90 seconds), target marked as unhealthy")

        # Kill nginx process to simulate crash
        crash_script = """
echo "Current nginx process status:"
sudo systemctl status nginx --no-pager -l || echo "Nginx not running"

echo -e "\\nKilling nginx process to simulate service crash..."
sudo pkill -9 nginx

echo -e "\\nWaiting 5 seconds..."
sleep 5

echo -e "\\nService status after crash:"
sudo systemctl status nginx --no-pager -l || echo "Nginx crashed (as expected)"

echo -e "\\nProcess check:"
ps aux | grep nginx | grep -v grep || echo "No nginx processes running"
"""

        print("\nExecuting crash simulation via AWS Systems Manager...")

        response = ssm.send_command(
            InstanceIds=[nginx_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [crash_script]},
            Comment="SRE Workshop Lab-01: Simulate nginx service crash",
        )

        command_id = response["Command"]["CommandId"]
        print(f"  Command ID: {command_id}")

        # Wait for command to complete
        print("  Waiting for crash simulation to complete...")
        time.sleep(10)

        result = ssm.get_command_invocation(CommandId=command_id, InstanceId=nginx_instance_id)

        if result["Status"] == "Success":
            return True
        else:
            print(f"  ❌ Command failed: {result['Status']}")
            if result.get("StandardErrorContent"):
                print(f"  Error: {result['StandardErrorContent']}")
            return False

    except Exception as e:
        print(f"❌ Nginx crash injection failed: {e}")
        return False


def inject_nginx_timeout(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Inject nginx timeout misconfiguration by setting proxy timeouts too short

    This simulates a common production issue where reverse proxy timeouts don't
    account for backend response times, causing 502 Bad Gateway errors.

    Args:
        resources: Dictionary of resource identifiers from get_stack_resources()
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        nginx_instance_id = resources.get("nginx_instance_id")

        if not nginx_instance_id:
            print("❌ Nginx instance ID not found in resources")
            return False

        # Create SSM client
        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            ssm = session.client("ssm")
        else:
            ssm = boto3.client("ssm", region_name=region_name)

        print(f"\nTarget EC2 instance: {nginx_instance_id}")
        print("\nInjecting nginx timeout misconfiguration...")
        print("  Technical details:")
        print("  - Setting proxy_read_timeout to 1 second (too short)")
        print("  - Backend queries taking >1s will trigger timeouts")
        print("  - Nginx returns 502 Bad Gateway when timeout occurs")
        print("  - Common issue when timeouts don't match backend SLAs")

        timeout_script = """
#!/bin/bash
set -e

# Backup original nginx.conf
sudo cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.backup

# Update nginx.conf with short timeouts
sudo sed -i 's/proxy_connect_timeout [0-9]*s;/proxy_connect_timeout 1s;/' /etc/nginx/nginx.conf
sudo sed -i 's/proxy_send_timeout [0-9]*s;/proxy_send_timeout 1s;/' /etc/nginx/nginx.conf
sudo sed -i 's/proxy_read_timeout [0-9]*s;/proxy_read_timeout 1s;/' /etc/nginx/nginx.conf

# Test configuration
sudo nginx -t

# Reload nginx to apply changes
sudo systemctl reload nginx

echo "Nginx timeout misconfiguration injected successfully"
"""

        print("\nExecuting timeout injection via AWS Systems Manager...")

        response = ssm.send_command(
            InstanceIds=[nginx_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [timeout_script]},
            Comment="SRE Workshop Lab-01: Inject nginx timeout misconfiguration",
        )

        command_id = response["Command"]["CommandId"]
        print(f"  Command ID: {command_id}")

        # Wait for command to complete
        print("  Waiting for injection to complete...")
        time.sleep(10)

        result = ssm.get_command_invocation(CommandId=command_id, InstanceId=nginx_instance_id)

        if result["Status"] == "Success":
            return True
        else:
            print(f"  ❌ Command failed: {result['Status']}")
            if result.get("StandardErrorContent"):
                print(f"  Error: {result['StandardErrorContent']}")
            return False

    except Exception as e:
        print(f"❌ Nginx timeout injection failed: {e}")
        return False
