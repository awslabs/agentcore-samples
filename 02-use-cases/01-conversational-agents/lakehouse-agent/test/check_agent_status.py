#!/usr/bin/env python3
"""
Check Agent Runtime Status and CloudWatch Logs

This script checks the status of the lakehouse agent runtime and helps you
find its CloudWatch logs.
"""

import boto3
from datetime import datetime, timedelta


def main():
    print("=" * 80)
    print("Agent Runtime Status and Logs Checker")
    print("=" * 80)

    session = boto3.Session()
    region = session.region_name

    print(f"\n📍 Region: {region}")

    # Check if agent runtime ARN exists in SSM
    print("\n🔍 Checking SSM Parameter Store...")
    ssm = boto3.client("ssm", region_name=region)

    try:
        runtime_arn = ssm.get_parameter(Name="/app/lakehouse-agent/agent-runtime-arn")["Parameter"]["Value"]
        print(f"   ✅ Agent Runtime ARN: {runtime_arn}")
    except ssm.exceptions.ParameterNotFound:
        print("   ❌ Agent runtime ARN not found in SSM")
        print("\n💡 Solution:")
        print("   The agent hasn't been deployed yet.")
        print("   Run: python lakehouse-agent/deploy_lakehouse_agent.py")
        return

    # Get agent runtime details
    print("\n🔍 Checking Agent Runtime Status...")
    try:
        client = boto3.client("bedrock-agentcore-control", region_name=region)
        response = client.get_agent_runtime(agentRuntimeArn=runtime_arn)
        runtime = response["agentRuntime"]

        status = runtime.get("status", "UNKNOWN")
        name = runtime.get("agentRuntimeName", "unknown")
        created = runtime.get("createdAt", "unknown")
        updated = runtime.get("updatedAt", "unknown")

        print(f"   Name: {name}")
        print(f"   Status: {status}")
        print(f"   Created: {created}")
        print(f"   Updated: {updated}")

        if status != "ACTIVE":
            print("\n   ⚠️  Agent is not ACTIVE!")
            print(f"   Current status: {status}")

            if status == "CREATING":
                print("   ℹ️  Agent is still being created. Wait a few minutes.")
            elif status == "FAILED":
                print("   ❌ Agent creation failed. Check CloudWatch logs for errors.")
            elif status == "UPDATING":
                print("   ℹ️  Agent is being updated. Wait a few minutes.")
        else:
            print("   ✅ Agent is ACTIVE and ready to receive requests")

        # Check authorizer configuration
        if "authorizerConfiguration" in runtime:
            auth_config = runtime["authorizerConfiguration"]
            if "customJWTAuthorizer" in auth_config:
                jwt_config = auth_config["customJWTAuthorizer"]
                print("\n   🔐 JWT Authentication:")
                print(f"      Discovery URL: {jwt_config.get('discoveryUrl')}")
                print(f"      Allowed Clients: {jwt_config.get('allowedClients')}")
            else:
                print("\n   🔐 Authentication: IAM SigV4")
        else:
            print("\n   🔐 Authentication: IAM SigV4 (default)")

    except Exception as e:
        print(f"   ❌ Error getting agent runtime: {e}")
        return

    # Find CloudWatch log groups
    print("\n🔍 Searching for CloudWatch Log Groups...")
    logs = boto3.client("logs", region_name=region)

    # Extract runtime ID from ARN
    # ARN format: arn:aws:bedrock-agentcore:region:account:runtime/runtime-id
    runtime_id = runtime_arn.split("/")[-1]

    # Common log group patterns for AgentCore Runtime

    found_log_groups = []

    # Search for log groups
    try:
        # Get all log groups with bedrock-agentcore prefix
        paginator = logs.get_paginator("describe_log_groups")

        for page in paginator.paginate(logGroupNamePrefix="/aws/bedrock-agentcore"):
            for log_group in page.get("logGroups", []):
                log_group_name = log_group["logGroupName"]
                found_log_groups.append(
                    {
                        "name": log_group_name,
                        "created": log_group.get("creationTime"),
                        "size": log_group.get("storedBytes", 0),
                    }
                )

        # Also try /aws/agentcore prefix
        for page in paginator.paginate(logGroupNamePrefix="/aws/agentcore"):
            for log_group in page.get("logGroups", []):
                log_group_name = log_group["logGroupName"]
                if log_group_name not in [lg["name"] for lg in found_log_groups]:
                    found_log_groups.append(
                        {
                            "name": log_group_name,
                            "created": log_group.get("creationTime"),
                            "size": log_group.get("storedBytes", 0),
                        }
                    )

    except Exception as e:
        print(f"   ⚠️  Error searching log groups: {e}")

    if found_log_groups:
        print(f"   ✅ Found {len(found_log_groups)} AgentCore log group(s):")
        for lg in found_log_groups:
            created_date = datetime.fromtimestamp(lg["created"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            size_mb = lg["size"] / (1024 * 1024)
            print(f"\n   📁 {lg['name']}")
            print(f"      Created: {created_date}")
            print(f"      Size: {size_mb:.2f} MB")

            # Check for recent log streams
            try:
                streams_response = logs.describe_log_streams(
                    logGroupName=lg["name"],
                    orderBy="LastEventTime",
                    descending=True,
                    limit=5,
                )

                streams = streams_response.get("logStreams", [])
                if streams:
                    print("      Recent log streams:")
                    for stream in streams[:3]:
                        stream_name = stream["logStreamName"]
                        last_event = stream.get("lastEventTimestamp")
                        if last_event:
                            last_event_date = datetime.fromtimestamp(last_event / 1000).strftime("%Y-%m-%d %H:%M:%S")
                            print(f"         - {stream_name} (last: {last_event_date})")
                        else:
                            print(f"         - {stream_name} (no events)")
                else:
                    print("      ⚠️  No log streams found (agent hasn't been invoked yet)")

            except Exception as e:
                print(f"      ⚠️  Error checking log streams: {e}")
    else:
        print("   ⚠️  No AgentCore log groups found")
        print("\n   This could mean:")
        print("   1. The agent hasn't been invoked yet (logs created on first invocation)")
        print("   2. CloudWatch logging isn't enabled")
        print("   3. The log group uses a different naming pattern")

    # Provide instructions for viewing logs
    print("\n📋 How to View Logs:")
    print("\n   Option 1: AWS Console")
    print("   1. Go to CloudWatch Console")
    print("   2. Click 'Log groups' in the left sidebar")
    print("   3. Search for: /aws/bedrock-agentcore")
    print(f"   4. Look for log groups containing: {runtime_id}")

    print("\n   Option 2: AWS CLI")
    if found_log_groups:
        log_group_name = found_log_groups[0]["name"]
        print("   # List log streams")
        print("   aws logs describe-log-streams \\")
        print(f"       --log-group-name '{log_group_name}' \\")
        print("       --order-by LastEventTime \\")
        print("       --descending \\")
        print("       --max-items 10")
        print("\n   # Tail logs (last 10 minutes)")
        print(f"   aws logs tail '{log_group_name}' --follow --since 10m")
    else:
        print("   # Search for log groups")
        print("   aws logs describe-log-groups \\")
        print("       --log-group-name-prefix '/aws/bedrock-agentcore'")

    print("\n   Option 3: Python Script")
    print("   python check_recent_logs.py")

    # Check if agent has been invoked
    print("\n🔍 Checking Invocation History...")

    if found_log_groups and any(lg["size"] > 0 for lg in found_log_groups):
        print("   ✅ Agent has been invoked (logs exist)")

        # Try to get recent log events
        for lg in found_log_groups:
            if lg["size"] > 0:
                try:
                    # Get recent log events
                    end_time = int(datetime.now().timestamp() * 1000)
                    start_time = int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)

                    events_response = logs.filter_log_events(
                        logGroupName=lg["name"],
                        startTime=start_time,
                        endTime=end_time,
                        limit=10,
                    )

                    events = events_response.get("events", [])
                    if events:
                        print(f"\n   📄 Recent log events from {lg['name']}:")
                        for event in events[:5]:
                            timestamp = datetime.fromtimestamp(event["timestamp"] / 1000).strftime("%H:%M:%S")
                            message = event["message"][:100]
                            print(f"      [{timestamp}] {message}")

                        if len(events) > 5:
                            print(f"      ... and {len(events) - 5} more events")
                except Exception as e:
                    print(f"   ⚠️  Error reading log events: {e}")
    else:
        print("   ⚠️  No invocations detected")
        print("\n   💡 To generate logs:")
        print("   1. Invoke the agent using the Streamlit UI")
        print("   2. Or run: python test_agent_invocation.py")
        print("   3. Then check CloudWatch logs again")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
