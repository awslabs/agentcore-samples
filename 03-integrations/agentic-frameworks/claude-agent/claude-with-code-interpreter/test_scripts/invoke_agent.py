import json
import uuid
import boto3
import logging
from botocore.exceptions import ClientError, ReadTimeoutError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

agent_arn = "<agent-arn>"

# Initialize the Amazon Bedrock AgentCore client
agent_core_client = boto3.client("bedrock-agentcore")


def _invoke(prompt: str, session_id: str):
    runtime_session_id = str(uuid.uuid4())
    payload = json.dumps({"prompt": prompt, "session_id": session_id}).encode()

    logger.info("*" * 80)
    logger.info("Sending request %s", payload)
    logger.info("*" * 80)

    # Invoke the agent
    try:
        response = agent_core_client.invoke_agent_runtime(
            agentRuntimeArn=agent_arn,
            runtimeSessionId=runtime_session_id,
            payload=payload,
            qualifier="DEFAULT",
        )
        streaming_body = response.get("response")
        final_response = ""
        final_session_id = ""

        # Stream chunks as they arrive
        logger.info("*" * 80)
        logger.info("Streaming response...")
        logger.info("*" * 80)

        try:
            for chunk in streaming_body.iter_lines():
                if chunk:
                    chunk_str = chunk.decode("utf-8")
                    # logger.info(f"\n********STREAMING CHUNK********* %s",chunk_str)
                    if not chunk_str or chunk_str.startswith(":"):
                        continue

                    # Handle SSE format
                    if chunk_str.startswith("data:"):
                        chunk_str = chunk_str.split(":", 1)[1].strip()

                    if chunk_str:
                        chunk_data = json.loads(chunk_str)
                        if chunk_data.get("type") == "text":
                            logger.info("\n TEXT : %s", chunk_data.get("text"))
                        elif chunk_data.get("type") == "tool_use":
                            logger.info("\n TOOL USED : %s", chunk_data.get("tool_name"))
                            logger.info("\n TOOL INPUT : %s", chunk_data.get("tool_input"))
                        elif chunk_data.get("type") == "final":
                            final_response = chunk_data.get("response", "")
                            final_session_id = chunk_data.get("session_id", "")
        except ReadTimeoutError as e:
            logger.error("Request failed: %s", str(e))

        logger.info("*" * 80)
        logger.info("Streaming complete")
        logger.info("*" * 80)

        return {
            "response": final_response if final_response else "No response received",
            "session_id": final_session_id,
        }

    except ClientError as e:
        logger.warning("Request failed: %s", str(e))
        raise


def _cleanup(session_id: str):
    logger.info("*" * 80 + "\n")
    logger.info("Cleaning up code interpreter session")
    ci_client = boto3.client("bedrock-agentcore", "us-west-2")
    response = ci_client.stop_code_interpreter_session(
        codeInterpreterIdentifier="aws.codeinterpreter.v1", sessionId=session_id
    )

    logger.info("*" * 80 + "\n")
    logger.info("Cleanup response.. %s \n", response)


def main():
    # List of prompts to test
    prompts = [
        # "Calculate the sum of numbers from 1 to 100",
        # "Write a Python function to check if a number is prime",
        "Create a sample data set of a retail store orders. Create a simple data analysis on a sample dataset. Save the files.",
    ]

    session_id = ""
    for prompt in prompts:
        logger.info("\nPrompt: %s", prompt)
        result = _invoke(prompt, session_id)
        # logger.info("*" * 80)
        # logger.info("FINAL RESPONSE")
        # logger.info("*" * 80)
        # logger.info("\n RESPONSE: %s", result['response'])
        logger.info("\n SESSION ID: %s", result["session_id"])
        session_id = result["session_id"]

    if session_id:
        _cleanup(session_id)


if __name__ == "__main__":
    main()
