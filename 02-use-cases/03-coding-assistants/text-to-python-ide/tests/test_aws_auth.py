#!/usr/bin/env python3
"""
Test script to verify AWS authentication setup
"""

import os
import boto3
from botocore.exceptions import NoCredentialsError, ProfileNotFound
from dotenv import load_dotenv


def test_aws_authentication():
    """Test AWS authentication with profile priority"""
    print("AWS Authentication Test")
    print("=" * 40)

    # Load environment variables
    load_dotenv()

    aws_profile = os.getenv("AWS_PROFILE", "default")
    aws_region = os.getenv("AWS_REGION", "us-east-1")

    print(f"AWS Profile: {aws_profile}")
    print(f"AWS Region: {aws_region}")
    print()

    # Test 1: Try AWS profile first
    print("1. Testing AWS Profile Authentication...")
    try:
        session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
        sts = session.client("sts")
        identity = sts.get_caller_identity()

        print("✅ AWS Profile authentication successful!")
        print(f"   Account: {identity.get('Account', 'Unknown')}")
        print(f"   User/Role: {identity.get('Arn', 'Unknown').split('/')[-1]}")
        print(f"   Region: {aws_region}")
        return session, "profile"

    except ProfileNotFound:
        print(f"⚠️  AWS profile '{aws_profile}' not found")
    except NoCredentialsError:
        print(f"⚠️  No credentials found for profile '{aws_profile}'")
    except Exception as e:
        print(f"⚠️  Profile authentication failed: {e}")

    # Test 2: Fallback to access keys
    print("\n2. Testing Access Key Authentication...")
    aws_access_key = os.getenv("AWS_ACCESS_KEY_ID")
    aws_secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")

    if aws_access_key and aws_secret_key:
        try:
            session = boto3.Session(
                aws_access_key_id=aws_access_key, aws_secret_access_key=aws_secret_key, region_name=aws_region
            )
            sts = session.client("sts")
            identity = sts.get_caller_identity()

            print("✅ Access key authentication successful!")
            print(f"   Account: {identity.get('Account', 'Unknown')}")
            print(f"   Access Key: {aws_access_key[:8]}...")
            print(f"   Region: {aws_region}")
            return session, "access_keys"

        except Exception as e:
            print(f"❌ Access key authentication failed: {e}")
    else:
        print("❌ No AWS access keys found in environment")

    print("\n❌ No valid AWS authentication method found!")
    return None, None


def test_bedrock_access(session):
    """Test access to Amazon Bedrock"""
    print("\n3. Testing Amazon Bedrock Access...")

    if not session:
        print("❌ No valid AWS session available")
        return False

    try:
        bedrock = session.client("bedrock")
        # Try to list foundation models
        response = bedrock.list_foundation_models()

        # Check if Claude models are available
        claude_models = [
            model for model in response.get("modelSummaries", []) if "claude" in model.get("modelId", "").lower()
        ]

        print("✅ Amazon Bedrock access successful!")
        print(f"   Available models: {len(response.get('modelSummaries', []))}")
        print(f"   Claude models: {len(claude_models)}")

        # Check for the specific model we use
        target_model = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
        model_available = any(model.get("modelId") == target_model for model in response.get("modelSummaries", []))

        if model_available:
            print(f"✅ Target model '{target_model}' is available")
        else:
            print(f"⚠️  Target model '{target_model}' not found")
            print("   Available Claude models:")
            for model in claude_models[:3]:  # Show first 3
                print(f"     - {model.get('modelId')}")

        return True

    except Exception as e:
        print(f"❌ Bedrock access failed: {e}")
        print("   Make sure your AWS account has access to Amazon Bedrock")
        return False


def test_agentcore_permissions(session):
    """Test AgentCore permissions"""
    print("\n4. Testing AgentCore Permissions...")

    if not session:
        print("❌ No valid AWS session available")
        return False

    try:
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        # Test code session creation and execution
        region = session.region_name or "us-east-1"

        with code_session(region) as code_client:
            # Try to execute a simple test
            response = code_client.invoke(
                "executeCode",
                {"code": "print('AgentCore permissions test successful')", "language": "python", "clearContext": True},
            )

            # Check if we got a valid response
            for event in response["stream"]:
                result = event.get("result", {})
                if not result.get("isError", False):
                    print("✅ AgentCore permissions verified!")
                    print("   You can use full AgentCore code execution")
                    return True

        return False

    except Exception as e:
        print(f"⚠️  AgentCore permissions not available: {e}")
        print("   The application will use Strands simulation instead")
        print("   This is normal if you don't have bedrock-agentcore permissions")
        return False


def main():
    """Run all authentication tests"""
    session, auth_method = test_aws_authentication()

    if session:
        bedrock_ok = test_bedrock_access(session)
        agentcore_ok = test_agentcore_permissions(session)

        print("\n" + "=" * 40)
        print("🎯 Authentication Summary:")
        print(f"   Method: {auth_method.title().replace('_', ' ')}")
        print(f"   Bedrock Access: {'✅' if bedrock_ok else '❌'}")
        print(f"   AgentCore Access: {'✅' if agentcore_ok else '⚠️'}")

        if bedrock_ok:
            print("\n🎉 Ready to run the application!")
            if agentcore_ok:
                print("   Full AgentCore code execution available")
            else:
                print("   Will use Strands simulation for code execution")
        else:
            print("\n❌ Bedrock access required. Check your AWS permissions.")

        return 0 if bedrock_ok else 1
    else:
        print("\n❌ AWS authentication failed. Check your credentials.")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
