#!/usr/bin/env python3
"""
Simple cleanup script for BedrockAgentCore resources.
Run this periodically to clean up AWS resources and avoid costs.
"""

import boto3
import sys
from datetime import datetime, timedelta


def cleanup_browsers(region="us-west-2"):
    """Delete all BedrockAgentCore browsers to stop charges."""
    print("🧹 Cleaning up browsers...")

    try:
        # This is a placeholder - the actual API endpoint might differ
        # You need to use the correct BedrockAgentCore control plane API
        from bedrock_agentcore._utils.endpoints import get_control_plane_endpoint

        control_client = boto3.client(
            "bedrock-agentcore-control",
            region_name=region,
            endpoint_url=get_control_plane_endpoint(region),
        )

        response = control_client.list_browsers()
        browsers = response.get("browsers", [])

        for browser in browsers:
            try:
                control_client.delete_browser(browserId=browser["browserId"])
                print(f"  ✅ Deleted browser: {browser['browserId']}")
            except Exception as e:
                print(f"  ❌ Failed to delete {browser['browserId']}: {e}")

        if not browsers:
            print("  ✓ No browsers to clean up")

    except Exception as e:
        print(f"  ⚠️  Could not list browsers: {e}")
        print("  Note: This might mean no browsers exist or API has changed")


def cleanup_old_s3_recordings(bucket_name, days_to_keep=7):
    """Delete S3 recordings older than specified days."""
    print(f"🧹 Cleaning S3 recordings older than {days_to_keep} days...")

    if not bucket_name:
        print("  ⚠️  No S3 bucket specified")
        return

    try:
        s3 = boto3.client("s3")
        cutoff = datetime.now() - timedelta(days=days_to_keep)

        response = s3.list_objects_v2(Bucket=bucket_name, Prefix="competitive_intel/")

        if "Contents" not in response:
            print("  ✓ No recordings found")
            return

        old_objects = []
        for obj in response["Contents"]:
            if obj["LastModified"].replace(tzinfo=None) < cutoff:
                old_objects.append({"Key": obj["Key"]})

        if old_objects:
            s3.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": old_objects[:1000]},  # Max 1000 at a time
            )
            print(f"  ✅ Deleted {len(old_objects)} old recordings")
        else:
            print("  ✓ No old recordings to delete")

    except Exception as e:
        print(f"  ❌ Error: {e}")


if __name__ == "__main__":
    import os

    print("=" * 50)
    print("BedrockAgentCore Resource Cleanup")
    print("=" * 50)

    # Get config from environment
    region = os.environ.get("AWS_REGION", "us-west-2")
    bucket = os.environ.get("S3_RECORDING_BUCKET", "")

    # Clean browsers (main cost driver)
    cleanup_browsers(region)

    # Clean old S3 recordings
    if "--delete-old-recordings" in sys.argv:
        cleanup_old_s3_recordings(bucket)
    else:
        print("\n💡 Tip: Add --delete-old-recordings to also clean S3")

    print("\n✅ Cleanup complete")
    print("=" * 50)
