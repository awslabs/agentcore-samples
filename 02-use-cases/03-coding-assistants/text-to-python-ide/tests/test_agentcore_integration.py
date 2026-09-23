#!/usr/bin/env python3
"""
Test script to verify the correct AgentCore integration following the official sample
"""

import os
import sys
from dotenv import load_dotenv


def test_agentcore_code_session():
    """Test AgentCore code_session functionality"""
    print("Testing AgentCore Code Session")
    print("=" * 40)

    load_dotenv()

    try:
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        print("✓ code_session import successful")

        aws_region = os.getenv("AWS_REGION", "us-east-1")
        print(f"Using region: {aws_region}")

        # Test code execution following the sample pattern
        test_code = "print('Hello from AgentCore!')\nresult = 2 + 2\nprint(f'2 + 2 = {result}')"

        try:
            with code_session(aws_region) as code_client:
                print("✓ Code session created successfully")

                response = code_client.invoke(
                    "executeCode", {"code": test_code, "language": "python", "clearContext": True}
                )

                print("✓ Code execution request sent")

                # Process response stream following the sample pattern
                for event in response["stream"]:
                    result = event.get("result", {})
                    if result.get("isError", False):
                        print(f"✗ Execution error: {result}")
                        return False
                    else:
                        structured_content = result.get("structuredContent", {})
                        stdout = structured_content.get("stdout", "")
                        if stdout:
                            print(f"✓ Execution output: {stdout.strip()}")

                print("✓ AgentCore code execution successful!")
                return True

        except Exception as e:
            print(f"⚠ AgentCore execution failed: {e}")
            print("  This is expected if you don't have bedrock-agentcore permissions")
            return False

    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_strands_with_agentcore_tool():
    """Test Strands-Agents agent with AgentCore tool following the sample pattern"""
    print("\nTesting Strands-Agents + AgentCore Integration")
    print("=" * 40)

    try:
        from strands import Agent, tool
        from strands.models import BedrockModel
        from bedrock_agentcore.tools.code_interpreter_client import code_session
        import json

        print("✓ All imports successful")

        aws_region = os.getenv("AWS_REGION", "us-east-1")

        # Create the execute_python tool following the exact sample pattern
        @tool
        def execute_python(code: str, description: str = "") -> str:
            """Execute Python code in the sandbox - following official sample pattern"""

            if description:
                code = f"# {description}\n{code}"

            print(f"\n Generated Code: {code}")

            try:
                with code_session(aws_region) as code_client:
                    response = code_client.invoke(
                        "executeCode", {"code": code, "language": "python", "clearContext": False}
                    )

                # Process response following the sample pattern
                for event in response["stream"]:
                    return json.dumps(event["result"])

            except Exception as e:
                return f"Execution failed: {str(e)}"

        print("✓ AgentCore tool created following sample pattern")

        # Create Bedrock model
        bedrock_model = BedrockModel(model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0", region_name=aws_region)
        print("✓ Bedrock model created")

        # Create Strands agent with AgentCore tool following the sample system prompt
        SYSTEM_PROMPT = """You are a helpful AI assistant that validates all answers through code execution.

VALIDATION PRINCIPLES:
1. When making claims about code, algorithms, or calculations - write code to verify them
2. Use execute_python to test mathematical calculations, algorithms, and logic
3. Create test scripts to validate your understanding before giving answers
4. Always show your work with actual code execution
5. If uncertain, explicitly state limitations and validate what you can

APPROACH:
- If asked about a programming concept, implement it in code to demonstrate
- If asked for calculations, compute them programmatically AND show the code
- If implementing algorithms, include test cases to prove correctness
- Document your validation process for transparency
- The sandbox maintains state between executions, so you can refer to previous results

TOOL AVAILABLE:
- execute_python: Run Python code and see output

RESPONSE FORMAT: The execute_python tool returns a JSON response with:
- sessionId: The sandbox session ID
- id: Request ID
- isError: Boolean indicating if there was an error
- content: Array of content objects with type and text/data
- structuredContent: For code execution, includes stdout, stderr, exitCode, executionTime"""

        agent = Agent(tools=[execute_python], system_prompt=SYSTEM_PROMPT, model=bedrock_model)
        print("✓ Strands-Agents agent with AgentCore tool created following sample pattern")

        # Test the integration with a simple query
        test_query = "Calculate 5 factorial using Python code"
        print(f"\nTesting with query: {test_query}")

        try:
            response = agent(test_query)
            print("✓ Agent response received")
            print(f"Response preview: {str(response)[:200]}...")
            return True

        except Exception as e:
            print(f"⚠ Agent execution failed: {e}")
            return False

    except Exception as e:
        print(f"✗ Integration test failed: {e}")
        return False


def main():
    """Run all AgentCore integration tests following the official sample"""
    print("AgentCore Integration Tests (Following Official Sample)")
    print("=" * 60)

    load_dotenv()

    tests = [test_agentcore_code_session, test_strands_with_agentcore_tool]

    passed = 0
    total = len(tests)

    for test in tests:
        try:
            if test():
                passed += 1
        except Exception as e:
            print(f"✗ Test failed with exception: {e}")
        print()

    print("=" * 60)
    print(f"Tests passed: {passed}/{total}")

    if passed == total:
        print("🎉 All AgentCore integration tests passed!")
        print("\nAgentCore Features Available:")
        print("✓ Real code execution in sandboxed environment")
        print("✓ Strands-Agents with AgentCore tools")
        print("✓ Following official sample pattern")
        return 0
    elif passed > 0:
        print("⚠ Partial success - some AgentCore features available")
        print("The application will work with available features")
        return 0
    else:
        print("❌ AgentCore integration not available")
        print("The application will use Strands simulation instead")
        return 1


if __name__ == "__main__":
    sys.exit(main())
