import boto3
import time
from botocore.exceptions import ClientError


def deploy_stack(stack_name, template_file, region, cf_client):
    """
    Deploy or update a CloudFormation stack for Customer Support Lambda and return outputs.

    Args:
        stack_name (str): Name of the CloudFormation stack
        template_file (str): Path to the CloudFormation template YAML file
        region (str): AWS region
        cf_client: Boto3 CloudFormation client

    Returns:
        tuple: (lambda_arn, gateway_role_arn, runtime_execution_role_arn)
    """

    # Read the template file
    try:
        with open(template_file, "r") as f:
            template_body = f.read()
        print(f"✅ Successfully read template file: {template_file}")
    except FileNotFoundError:
        raise FileNotFoundError(f"❌ Template file not found: {template_file}")
    except Exception as e:
        raise Exception(f"❌ Error reading template file: {str(e)}")

    # Check if stack exists
    stack_exists = False
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        stack_status = response["Stacks"][0]["StackStatus"]
        stack_exists = True
        print(f"📋 Stack '{stack_name}' exists with status: {stack_status}")

        # Check if stack is in a failed state
        if stack_status in ["CREATE_FAILED", "ROLLBACK_COMPLETE", "ROLLBACK_FAILED"]:
            print(f"⚠️  Stack is in {stack_status} state. You may need to delete it first.")

    except ClientError as e:
        if "does not exist" in str(e):
            print(f"🆕 Stack '{stack_name}' does not exist. Will create new stack...")
        else:
            raise

    try:
        if stack_exists:
            # Update existing stack
            print(f"🔄 Updating stack '{stack_name}'...")
            response = cf_client.update_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                Tags=[
                    {"Key": "Application", "Value": "CustomerSupport"},
                    {"Key": "ManagedBy", "Value": "CloudFormation"},
                ],
            )
            print(f"✅ Stack update initiated. Stack ID: {response['StackId']}")
            waiter = cf_client.get_waiter("stack_update_complete")
            wait_message = "Waiting for stack update to complete"

        else:
            # Create new stack
            print(f"🚀 Creating stack '{stack_name}'...")
            response = cf_client.create_stack(
                StackName=stack_name,
                TemplateBody=template_body,
                Capabilities=["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                Tags=[
                    {"Key": "Application", "Value": "CustomerSupport"},
                    {"Key": "ManagedBy", "Value": "CloudFormation"},
                ],
                OnFailure="ROLLBACK",
            )
            print(f"✅ Stack creation initiated. Stack ID: {response['StackId']}")
            waiter = cf_client.get_waiter("stack_create_complete")
            wait_message = "Waiting for stack creation to complete"

        # Wait for stack operation to complete with progress updates
        print(f"⏳ {wait_message}...")
        print("   This may take several minutes as it creates:")
        print("   - DynamoDB tables (WarrantyTable, CustomerProfileTable)")
        print("   - IAM Roles (AgentCore, Gateway, Lambda roles)")
        print("   - Lambda functions (CustomerSupportLambda, PopulateDataFunction)")
        print("   - Custom resource to populate synthetic data")

        waiter.wait(
            StackName=stack_name,
            WaiterConfig={
                "Delay": 15,  # Check every 15 seconds
                "MaxAttempts": 120,  # Wait up to 30 minutes
            },
        )
        print("✅ Stack operation completed successfully!")

    except ClientError as e:
        error_message = str(e)

        if "No updates are to be performed" in error_message:
            print("ℹ️  No updates needed - stack is already up to date.")
        elif "ValidationError" in error_message:
            print(f"❌ Validation error: {error_message}")
            raise
        else:
            print(f"❌ Error during stack operation: {error_message}")
            # Try to get stack events for debugging
            try:
                print("\n📋 Recent stack events:")
                events = cf_client.describe_stack_events(StackName=stack_name)
                for event in events["StackEvents"][:5]:
                    if "FAILED" in event.get("ResourceStatus", ""):
                        print(
                            f"   ❌ {event['LogicalResourceId']}: {event.get('ResourceStatusReason', 'No reason provided')}"
                        )
            except Exception:
                pass
            raise
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        raise

    # Get stack outputs
    print("\n📤 Retrieving stack outputs...")
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        outputs = response["Stacks"][0].get("Outputs", [])

        if not outputs:
            raise Exception("❌ No outputs found in stack. Stack may have failed to create properly.")

        # Extract specific outputs based on your template
        lambda_arn = None
        gateway_role_arn = None
        runtime_execution_role_arn = None

        for output in outputs:
            key = output["OutputKey"]
            value = output["OutputValue"]

            if key == "CustomerSupportLambdaArn":
                lambda_arn = value
                print(f"   ✅ Lambda ARN: {value}")
            elif key == "GatewayAgentCoreRoleArn":
                gateway_role_arn = value
                print(f"   ✅ Gateway Role ARN: {value}")
            elif key == "AgentCoreRuntimeExecutionRoleArn":
                runtime_execution_role_arn = value
                print(f"   ✅ Runtime Execution Role ARN: {value}")

        # Verify all required outputs were found
        missing_outputs = []
        if not lambda_arn:
            missing_outputs.append("CustomerSupportLambdaArn")
        if not gateway_role_arn:
            missing_outputs.append("GatewayAgentCoreRoleArn")
        if not runtime_execution_role_arn:
            missing_outputs.append("AgentCoreRuntimeExecutionRoleArn")

        if missing_outputs:
            raise Exception(f"❌ Missing required outputs: {', '.join(missing_outputs)}")

        print("\n🎉 Stack deployment completed successfully!")
        print(f"   Stack Name: {stack_name}")
        print(f"   Region: {region}")

        return lambda_arn, gateway_role_arn, runtime_execution_role_arn

    except ClientError as e:
        print(f"❌ Error retrieving stack outputs: {str(e)}")
        raise
    except Exception as e:
        print(f"❌ Error processing stack outputs: {str(e)}")
        raise


def delete_stack(stack_name, region, cf_client, wait=True):
    """
    Delete a CloudFormation stack and all its resources.

    Args:
        stack_name (str): Name of the CloudFormation stack to delete
        region (str): AWS region
        cf_client: Boto3 CloudFormation client
        wait (bool): Whether to wait for deletion to complete (default: True)

    Returns:
        bool: True if deletion was successful, False otherwise
    """

    print(f"🗑️  Preparing to delete stack: {stack_name}")
    print(f"   Region: {region}")
    print("=" * 80)

    # Check if stack exists
    try:
        response = cf_client.describe_stacks(StackName=stack_name)
        stack_status = response["Stacks"][0]["StackStatus"]
        print(f"📋 Current stack status: {stack_status}")

        # Check if stack is already being deleted
        if stack_status == "DELETE_IN_PROGRESS":
            print("⏳ Stack deletion already in progress...")
            if wait:
                return _wait_for_deletion(stack_name, cf_client)
            return True

        # Check if stack is in a failed state
        if stack_status == "DELETE_FAILED":
            print("⚠️  Stack is in DELETE_FAILED state. Will attempt to retry deletion...")

    except ClientError as e:
        if "does not exist" in str(e):
            print(f"ℹ️  Stack '{stack_name}' does not exist. Nothing to delete.")
            return True
        else:
            print(f"❌ Error checking stack status: {str(e)}")
            raise

    # Get resources before deletion for reporting
    try:
        print("\n📦 Resources to be deleted:")
        resources = cf_client.list_stack_resources(StackName=stack_name)
        resource_summary = {}

        for resource in resources["StackResourceSummaries"]:
            resource_type = resource["ResourceType"]
            logical_id = resource["LogicalResourceId"]
            physical_id = resource.get("PhysicalResourceId", "N/A")

            if resource_type not in resource_summary:
                resource_summary[resource_type] = []
            resource_summary[resource_type].append({"logical": logical_id, "physical": physical_id})

        for resource_type, items in sorted(resource_summary.items()):
            print(f"\n   {resource_type}:")
            for item in items:
                print(f"      - {item['logical']}")
                if resource_type == "AWS::DynamoDB::Table":
                    print(f"        ⚠️  Table: {item['physical']} (all data will be deleted)")
                elif resource_type == "AWS::Lambda::Function":
                    print(f"        🔧 Function: {item['physical']}")
                elif resource_type == "AWS::IAM::Role":
                    print(f"        🔐 Role: {item['physical']}")

        # Check for DynamoDB tables with data
        dynamodb_tables = resource_summary.get("AWS::DynamoDB::Table", [])
        if dynamodb_tables:
            print(f"\n⚠️  WARNING: This will delete {len(dynamodb_tables)} DynamoDB table(s) and ALL their data!")
            dynamodb = boto3.client("dynamodb", region_name=region)
            for table in dynamodb_tables:
                try:
                    table_name = table["physical"]
                    response = dynamodb.scan(TableName=table_name, Select="COUNT", Limit=1)
                    if response["Count"] > 0:
                        print(f"      ⚠️  {table_name} contains data!")
                except Exception:
                    pass

    except ClientError as e:
        print(f"⚠️  Could not list resources: {str(e)}")

    # Confirm deletion
    print("\n" + "=" * 80)
    print("⚠️  THIS ACTION CANNOT BE UNDONE!")
    print("=" * 80)

    # Initiate stack deletion
    try:
        print("\n🚀 Initiating stack deletion...")
        cf_client.delete_stack(StackName=stack_name)
        print("✅ Delete request submitted successfully")

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_message = e.response["Error"]["Message"]

        if error_code == "ValidationError" and "does not exist" in error_message:
            print(f"ℹ️  Stack '{stack_name}' does not exist.")
            return True
        else:
            print(f"❌ Error initiating stack deletion: {error_message}")
            return False

    # Wait for deletion if requested
    if wait:
        return _wait_for_deletion(stack_name, cf_client)
    else:
        print("\nℹ️  Stack deletion initiated but not waiting for completion.")
        return True


def _wait_for_deletion(stack_name, cf_client, max_wait_minutes=30):
    """
    Internal function to wait for stack deletion to complete.

    Args:
        stack_name (str): Name of the stack
        cf_client: CloudFormation client
        max_wait_minutes (int): Maximum time to wait in minutes

    Returns:
        bool: True if deletion completed successfully
    """
    print("\n⏳ Waiting for stack deletion to complete...")
    print(f"   This may take up to {max_wait_minutes} minutes")
    print("   Checking status every 15 seconds...")

    start_time = time.time()
    max_wait_seconds = max_wait_minutes * 60
    check_interval = 15
    last_status = None
    dots = 0

    try:
        while True:
            elapsed = time.time() - start_time

            if elapsed > max_wait_seconds:
                print(f"\n⚠️  Timeout: Stack deletion took longer than {max_wait_minutes} minutes")
                print("   Check AWS Console for current status")
                return False

            try:
                response = cf_client.describe_stacks(StackName=stack_name)
                current_status = response["Stacks"][0]["StackStatus"]

                # Print status if it changed
                if current_status != last_status:
                    print(f"\n   Status: {current_status}")
                    last_status = current_status
                    dots = 0
                else:
                    # Print dots to show progress
                    print(".", end="", flush=True)
                    dots += 1
                    if dots >= 20:
                        print()
                        dots = 0

                # Check for deletion failures
                if current_status == "DELETE_FAILED":
                    print("\n❌ Stack deletion failed!")
                    _print_deletion_errors(stack_name, cf_client)
                    return False

                # Still deleting
                if current_status == "DELETE_IN_PROGRESS":
                    time.sleep(check_interval)
                    continue

                # Unexpected status
                print(f"\n⚠️  Unexpected status: {current_status}")
                return False

            except ClientError as e:
                if "does not exist" in str(e):
                    # Stack successfully deleted
                    print(f"\n✅ Stack '{stack_name}' deleted successfully!")
                    elapsed_minutes = elapsed / 60
                    print(f"   Total time: {elapsed_minutes:.1f} minutes")
                    return True
                else:
                    # Some other error
                    print(f"\n❌ Error checking stack status: {str(e)}")
                    return False

    except KeyboardInterrupt:
        print("\n\n⚠️  Deletion monitoring interrupted by user")
        print("   Stack deletion will continue in the background")
        return False


def _print_deletion_errors(stack_name, cf_client):
    """
    Internal function to print detailed error messages for failed stack deletion.
    """
    try:
        print("\n📋 Deletion failure details:")
        events = cf_client.describe_stack_events(StackName=stack_name)

        failed_events = [event for event in events["StackEvents"] if "FAILED" in event.get("ResourceStatus", "")]

        if failed_events:
            for event in failed_events[:10]:  # Show last 10 failed events
                resource_type = event.get("ResourceType", "Unknown")
                logical_id = event.get("LogicalResourceId", "Unknown")
                reason = event.get("ResourceStatusReason", "No reason provided")

                print(f"\n   ❌ {resource_type} - {logical_id}")
                print(f"      Reason: {reason}")

        print("\n💡 Troubleshooting tips:")
        print("   1. Some resources may have dependencies preventing deletion")
        print("   2. Check if DynamoDB tables have deletion protection enabled")
        print("   3. Verify Lambda functions are not being invoked")
        print("   4. Try deleting the stack again after a few minutes")

    except Exception as e:
        print(f"   Could not retrieve error details: {str(e)}")


# ============================================================================
# USAGE EXAMPLE
# ============================================================================

if __name__ == "__main__":
    import boto3

    # Initialize
    session = boto3.Session()
    region = session.region_name
    stack_name = "customer-support-lambda-stack"
    template_file = "cloudformation/customer_support_lambda.yaml"
    cf_client = boto3.client("cloudformation", region_name=region)

    print("=" * 80)
    print("CLOUDFORMATION STACK MANAGEMENT")
    print("=" * 80)

    # Deploy the CloudFormation stack
    print("\n🚀 DEPLOYING STACK...")
    print("=" * 80)

    try:
        lambda_arn, gateway_role_arn, runtime_execution_role_arn = deploy_stack(
            stack_name=stack_name,
            template_file=template_file,
            region=region,
            cf_client=cf_client,
        )

        print("\n" + "=" * 80)
        print("📋 DEPLOYMENT SUMMARY")
        print("=" * 80)
        print("\n🔧 Lambda Function ARN:")
        print(f"   {lambda_arn}")
        print("\n🔐 Gateway Role ARN:")
        print(f"   {gateway_role_arn}")
        print("\n🔐 Runtime Execution Role ARN:")
        print(f"   {runtime_execution_role_arn}")

    except Exception as e:
        print(f"\n❌ Deployment failed: {str(e)}")
        exit(1)

    # Optional: Uncomment to delete the stack
    # print("\n\n🗑️  DELETING STACK...")
    # print("=" * 80)
    #
    # success = delete_stack(
    #     stack_name=stack_name,
    #     region=region,
    #     cf_client=cf_client,
    #     wait=True
    # )
    #
    # if success:
    #     print("\n🎉 Stack deleted successfully!")
    # else:
    #     print("\n❌ Stack deletion failed")
