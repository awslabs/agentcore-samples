"""
Helper functions to retrieve infrastructure resource IDs from SSM Parameter Store
"""

import boto3
from typing import Dict
from botocore.exceptions import ClientError


def get_stack_resources(region_name: str, profile_name: str = None) -> Dict[str, str]:
    """
    Retrieve key resource identifiers from the CloudFormation stack via SSM.

    Args:
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Dictionary of resource identifiers
    """
    try:
        # Create session with profile if provided
        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            ssm = session.client("ssm")
            ec2 = session.client("ec2")
            iam = session.client("iam")
        else:
            ssm = boto3.client("ssm", region_name=region_name)
            ec2 = boto3.client("ec2", region_name=region_name)
            iam = boto3.client("iam", region_name=region_name)

        resources = {}

        # SSM parameter mappings created by the CloudFormation template
        ssm_mappings = {
            "nginx_instance_id": "/sre-workshop/ec2/nginx-instance-id",
            "app_instance_id": "/sre-workshop/ec2/app-instance-id",
            #'metrics_table_name': '/sre-workshop/dynamodb/metrics-table-name',
            #'incidents_table_name': '/sre-workshop/dynamodb/incidents-table-name',
            "crm_activities_table_name": "/sre-workshop/dynamodb/crm-activities-table-name",
            "crm_customers_table_name": "/sre-workshop/dynamodb/crm-customers-table-name",
            "crm_deals_table_name": "/sre-workshop/dynamodb/crm-deals-table-name",
            "vpc_id": "/sre-workshop/vpc/vpc-id",
            "public_alb_dns": "/sre-workshop/alb/public-dns",
            "private_alb_dns": "/sre-workshop/alb/private-dns",
        }

        for key, param_name in ssm_mappings.items():
            try:
                response = ssm.get_parameter(Name=param_name)
                resources[key] = response["Parameter"]["Value"]
            except ClientError as e:
                print(f"  ⚠️  Could not retrieve {param_name}: {e}")

        # Get EC2 instance role name (needed for IAM operations)
        if resources.get("app_instance_id"):
            try:
                instance_info = ec2.describe_instances(InstanceIds=[resources["app_instance_id"]])
                instance_profile = instance_info["Reservations"][0]["Instances"][0].get("IamInstanceProfile")
                if instance_profile:
                    profile_name = instance_profile["Arn"].split("/")[-1]
                    profile_info = iam.get_instance_profile(InstanceProfileName=profile_name)
                    resources["ec2_role_name"] = profile_info["InstanceProfile"]["Roles"][0]["RoleName"]
            except Exception as e:
                print(f"  ⚠️  Could not retrieve EC2 role name: {e}")

        return resources

    except Exception as e:
        print(f"❌ Failed to retrieve stack resources: {e}")
        return {}
