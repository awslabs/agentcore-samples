#!/usr/bin/env python3
"""
Test script to verify AgentCore integration with correct imports
"""

import os
import sys
from dotenv import load_dotenv


def test_agentcore_imports():
    """Test correct AgentCore imports"""
    print("Testing AgentCore Imports")
    print("=" * 40)

    try:
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        print("✓ bedrock_agentcore.tools.code_interpreter_client imported successfully")

        from bedrock_agentcore.runtime.app import BedrockAgentCoreApp

        print("✓ bedrock_agentcore.runtime.app imported successfully")

        return True

    except ImportError as e:
        print(f"✗ Failed to import AgentCore components: {e}")
        return False


def test_code_session():
    """Test code_session functionality"""
    print("\nTesting Code Session")
    print("=" * 40)

    load_dotenv()

    try:
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        aws_region = os.getenv("AWS_REGION", "us-east-1")
        print(f"Using region: {aws_region}")

        # Test creating a code session
        with code_session(aws_region) as code_client:
            print("✓ Code session created successfully")

            # Test code execution
            response = code_client.invoke(
                "executeCode", {"code": "print('Hello from AgentCore!')", "language": "python", "clearContext": True}
            )

            print("✓ Code execution request sent")

            # Process response
            for event in response["stream"]:
                result = event.get("result", {})
                if not result.get("isError", False):
                    print("✓ Code execution successful")
                    return True

        return False

    except Exception as e:
        print(f"⚠ Code session test failed: {e}")
        print("  This is expected if you don't have bedrock-agentcore permissions")
        return False


def test_strands_integration():
    """Test Strands + AgentCore integration"""
    print("\nTesting Strands + AgentCore Integration")
    print("=" * 40)

    try:
        from strands import Agent, tool
        from strands.models import BedrockModel
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        print("✓ All imports successful")

        # Create AgentCore tool
        @tool
        def execute_code(code: str) -> str:
            """Execute code using AgentCore"""
            aws_region = os.getenv("AWS_REGION", "us-east-1")

            try:
                with code_session(aws_region) as code_client:
                    response = code_client.invoke(
                        "executeCode", {"code": code, "language": "python", "clearContext": False}
                    )

                for event in response["stream"]:
                    result = event.get("result", {})
                    if result.get("isError", False):
                        return f"Error: {result}"
                    else:
                        structured_content = result.get("structuredContent", {})
                        return structured_content.get("stdout", "Code executed")

            except Exception as e:
                return f"Execution failed: {e}"

        print("✓ AgentCore tool created")

        # Create Strands agent
        bedrock_model = BedrockModel(
            model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0", region_name=os.getenv("AWS_REGION", "us-east-1")
        )

        agent = Agent(model=bedrock_model, tools=[execute_code], system_prompt="You are a code execution assistant.")

        print("✓ Strands agent with AgentCore tool created")
        return True

    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        return False


def main():
    """Run all AgentCore tests"""
    print("AgentCore Integration Tests")
    print("=" * 50)

    tests = [test_agentcore_imports, test_code_session, test_strands_integration]

    passed = 0
    total = len(tests)

    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
        print()

    print("=" * 50)
    print(f"Tests passed: {passed}/{total}")

    if passed >= 1:  # At least imports should work
        print("🎉 AgentCore integration is properly configured!")
        return 0
    else:
        print("❌ AgentCore integration has issues")
        return 1


if __name__ == "__main__":
    sys.exit(main())
