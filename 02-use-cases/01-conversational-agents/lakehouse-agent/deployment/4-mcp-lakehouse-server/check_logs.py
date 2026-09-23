#!/usr/bin/env python3
"""
Check CloudWatch Logs for MCP Server

This script helps you find and view logs from the MCP server running on AgentCore Runtime.
"""

import boto3
import sys
from datetime import datetime, timedelta


def main():
    print("=" * 70)
    print("CloudWatch Logs Checker for MCP Server")
    print("=" * 70)

    # Get region
    session = boto3.Session()
    region = session.region_name

    # Initialize clients
    ssm = boto3.client("ssm", region_name=region)
    logs = boto3.client("logs", region_name=region)

    print(f"\n✅ Using region: {region}")

    # Get runtime ARN from SSM
    try:
        runtime_arn = ssm.get_parameter(Name="/app/lakehouse-agent/mcp-server-runtime-arn")["Parameter"]["Value"]
        print(f"✅ Runtime ARN: {runtime_arn}")

        # Extract runtime ID from ARN
        runtime_id = runtime_arn.split("/")[-1]
        print(f"✅ Runtime ID: {runtime_id}")
    except Exception as e:
        print("❌ Error: Could not find runtime ARN in SSM")
        print("   Parameter: /app/lakehouse-agent/mcp-server-runtime-arn")
        print(f"   Error: {e}")
        sys.exit(1)

    # Search for log groups
    print("\n🔍 Searching for log groups...")

    try:
        # Try different log group patterns
        patterns = [
            f"/aws/bedrock-agentcore/runtime/{runtime_id}",
            f"/aws/bedrock-agentcore/{runtime_id}",
            f"/aws/agentcore/runtime/{runtime_id}",
            f"/aws/agentcore/{runtime_id}",
            "/aws/bedrock-agentcore",
            "/aws/agentcore",
        ]

        found_groups = []

        for pattern in patterns:
            try:
                response = logs.describe_log_groups(logGroupNamePrefix=pattern, limit=50)

                for group in response.get("logGroups", []):
                    log_group_name = group["logGroupName"]
                    if log_group_name not in [g["logGroupName"] for g in found_groups]:
                        found_groups.append(group)
                        print(f"   ✅ Found: {log_group_name}")
            except Exception:
                pass

        if not found_groups:
            print("\n⚠️  No log groups found matching the patterns")
            print("\n💡 Trying to list all bedrock-agentcore log groups...")

            response = logs.describe_log_groups(logGroupNamePrefix="/aws/bedrock", limit=50)

            print("\n📋 All bedrock-related log groups:")
            for group in response.get("logGroups", []):
                print(f"   - {group['logGroupName']}")

            response = logs.describe_log_groups(logGroupNamePrefix="/aws/agentcore", limit=50)

            print("\n📋 All agentcore-related log groups:")
            for group in response.get("logGroups", []):
                print(f"   - {group['logGroupName']}")

            sys.exit(0)

        # Show log streams for each group
        print("\n📋 Log Groups and Streams:")
        for group in found_groups:
            log_group_name = group["logGroupName"]
            print(f"\n   Log Group: {log_group_name}")

            try:
                streams_response = logs.describe_log_streams(
                    logGroupName=log_group_name,
                    orderBy="LastEventTime",
                    descending=True,
                    limit=5,
                )

                streams = streams_response.get("logStreams", [])
                if streams:
                    print("   Recent streams:")
                    for stream in streams:
                        last_event = stream.get("lastEventTimestamp", 0)
                        if last_event:
                            last_event_time = datetime.fromtimestamp(last_event / 1000)
                            print(f"      - {stream['logStreamName']}")
                            print(f"        Last event: {last_event_time}")
                else:
                    print("      No log streams found")
            except Exception as e:
                print(f"      Error listing streams: {e}")

        # Ask user which log group to view
        if len(found_groups) == 1:
            selected_group = found_groups[0]["logGroupName"]
            print(f"\n📖 Viewing logs from: {selected_group}")
        else:
            print("\n❓ Which log group would you like to view?")
            for i, group in enumerate(found_groups, 1):
                print(f"   {i}. {group['logGroupName']}")

            choice = input(f"\nEnter number (1-{len(found_groups)}) or press Enter for first: ").strip()

            if choice:
                try:
                    idx = int(choice) - 1
                    selected_group = found_groups[idx]["logGroupName"]
                except (ValueError, IndexError):
                    selected_group = found_groups[0]["logGroupName"]
            else:
                selected_group = found_groups[0]["logGroupName"]

            print(f"\n📖 Viewing logs from: {selected_group}")

        # Get recent logs
        print("\n📜 Recent log events (last 10 minutes):")
        print("=" * 70)

        start_time = int((datetime.now() - timedelta(minutes=10)).timestamp() * 1000)

        try:
            response = logs.filter_log_events(logGroupName=selected_group, startTime=start_time, limit=100)

            events = response.get("events", [])

            if events:
                for event in events:
                    timestamp = datetime.fromtimestamp(event["timestamp"] / 1000)
                    message = event["message"].rstrip()
                    print(f"[{timestamp}] {message}")
            else:
                print("No recent log events found")
                print("\n💡 Try invoking the MCP server and run this script again")
        except Exception as e:
            print(f"❌ Error fetching log events: {e}")

        print("=" * 70)

        # Show AWS Console link
        console_url = f"https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{selected_group.replace('/', '$252F')}"
        print("\n🔗 View in AWS Console:")
        print(f"   {console_url}")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✨ Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
