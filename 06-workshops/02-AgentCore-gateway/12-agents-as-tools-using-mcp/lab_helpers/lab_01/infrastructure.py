"""
Infrastructure verification functions for Lab 01
"""

import boto3
from typing import Dict
from botocore.exceptions import ClientError


def verify_ec2_instances(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Verify EC2 instances are running

    Args:
        resources: Dictionary of resource identifiers
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        print("1. Verifying EC2 Instances...")

        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            ec2 = session.client("ec2")
        else:
            ec2 = boto3.client("ec2", region_name=region_name)

        nginx_id = resources.get("nginx_instance_id")
        app_id = resources.get("app_instance_id")

        if not nginx_id or not app_id:
            print("  ❌ Instance IDs not found")
            return False

        response = ec2.describe_instance_status(InstanceIds=[nginx_id, app_id], IncludeAllInstances=True)

        all_running = True
        for instance in response["InstanceStatuses"]:
            instance_id = instance["InstanceId"]
            state = instance["InstanceState"]["Name"]
            status = instance.get("InstanceStatus", {}).get("Status", "unknown")

            if state == "running" and status == "ok":
                print(f"  ✅ Instance {instance_id}: {state} ({status})")
            else:
                print(f"  ⚠️  Instance {instance_id}: {state} ({status})")
                all_running = False

        return all_running

    except Exception as e:
        print(f"  ❌ EC2 verification failed: {e}")
        return False


def verify_dynamodb_tables(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Verify DynamoDB tables exist and are accessible

    Args:
        resources: Dictionary of resource identifiers
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        print("\n2. Verifying DynamoDB Tables...")

        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            dynamodb = session.client("dynamodb")
        else:
            dynamodb = boto3.client("dynamodb", region_name=region_name)

        metrics_table = resources.get("metrics_table_name")  # noqa: F841
        incidents_table = resources.get("incidents_table_name")  # noqa: F841
        crm_activities_table = resources.get("crm_activities_table_name")
        crm_customers_table = resources.get("crm_customers_table_name")
        crm_deals_table = resources.get("crm_deals_table_name")

        if not crm_activities_table or not crm_customers_table or not crm_deals_table:
            print("  ❌ Table names not found")
            return False

        all_active = True
        for table_name in [crm_activities_table, crm_customers_table, crm_deals_table]:
            try:
                response = dynamodb.describe_table(TableName=table_name)
                status = response["Table"]["TableStatus"]
                billing_mode = response["Table"]["BillingModeSummary"]["BillingMode"]

                if status == "ACTIVE":
                    print(
                        f"  ✅ Table {table_name}: {status} ({billing_mode})"
                    )  # codeql[py/clear-text-logging-sensitive-data]
                else:
                    print(f"  ⚠️  Table {table_name}: {status}")
                    all_active = False
            except ClientError as e:
                print(f"  ❌ Table {table_name}: {e}")
                all_active = False

        return all_active

    except Exception as e:
        print(f"  ❌ DynamoDB verification failed: {e}")
        return False


def verify_alb_health(resources: Dict[str, str], region_name: str, profile_name: str = None) -> bool:
    """
    Verify ALB target health

    Args:
        resources: Dictionary of resource identifiers
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        print("\n3. Verifying ALB Target Health...")

        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            elbv2 = session.client("elbv2")
        else:
            elbv2 = boto3.client("elbv2", region_name=region_name)

        # Get all load balancers
        albs = elbv2.describe_load_balancers()

        sre_albs = [alb for alb in albs["LoadBalancers"] if "sre-workshop" in alb["LoadBalancerName"]]

        if not sre_albs:
            print("  ❌ No SRE workshop ALBs found")
            return False

        all_healthy = True
        for alb in sre_albs:
            alb_name = alb["LoadBalancerName"]  # noqa: F841

            # Get target groups for this ALB
            target_groups = elbv2.describe_target_groups(LoadBalancerArn=alb["LoadBalancerArn"])

            for tg in target_groups["TargetGroups"]:
                tg_name = tg["TargetGroupName"]
                tg_arn = tg["TargetGroupArn"]

                # Get target health
                health_response = elbv2.describe_target_health(TargetGroupArn=tg_arn)

                for target_health in health_response["TargetHealthDescriptions"]:
                    target = target_health["Target"]
                    health = target_health["TargetHealth"]

                    target_id = target["Id"]
                    health_state = health["State"]

                    if health_state == "healthy":
                        print(f"  ✅ {tg_name}/{target_id}: {health_state}")
                    else:
                        print(f"  ⚠️  {tg_name}/{target_id}: {health_state}")
                        all_healthy = False

        return all_healthy

    except Exception as e:
        print(f"  ❌ ALB verification failed: {e}")
        return False


def verify_cloudwatch_logs(region_name: str, profile_name: str = None) -> bool:
    """
    Verify CloudWatch log groups exist

    Args:
        region_name: AWS region
        profile_name: AWS profile name (optional)

    Returns:
        Boolean indicating success/failure
    """
    try:
        print("\n4. Verifying CloudWatch Log Groups...")

        if profile_name:
            session = boto3.Session(profile_name=profile_name, region_name=region_name)
            logs = session.client("logs")
        else:
            logs = boto3.client("logs", region_name=region_name)

        required_log_groups = [
            "/aws/sre-workshop/application",
            "/aws/sre-workshop/nginx/access",
            "/aws/sre-workshop/nginx/error",
        ]

        all_exist = True
        for log_group_name in required_log_groups:
            try:
                response = logs.describe_log_groups(logGroupNamePrefix=log_group_name, limit=1)

                if response["logGroups"]:
                    print(f"  ✅ Log group exists: {log_group_name}")
                else:
                    print(f"  ⚠️  Log group not found: {log_group_name}")
                    all_exist = False
            except ClientError as e:
                print(f"  ❌ Error checking {log_group_name}: {e}")
                all_exist = False

        return all_exist

    except Exception as e:
        print(f"  ❌ CloudWatch verification failed: {e}")
        return False


def get_app_url():
    url = ""
    cfn = boto3.client("cloudformation")
    elbv2 = boto3.client("elbv2")

    # Get all resources in a stack
    response = cfn.list_stack_resources(StackName="sre-agent-workshop")

    for resource in response["StackResourceSummaries"]:
        if resource["LogicalResourceId"] == "PublicALB":
            response = elbv2.describe_load_balancers(LoadBalancerArns=[resource["PhysicalResourceId"]])
            dns_name = response["LoadBalancers"][0]["DNSName"]
            url = f"http://{dns_name}:8080"
    return url
