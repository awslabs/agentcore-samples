import json
import time
import boto3
from botocore.exceptions import ClientError
import logging
from .models import CodeIntExecutionResult

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CodeInterpreterClient:
    """Client for AgentCore Code Interpreter."""

    def __init__(self, region_name: str = "us-west-2"):
        self.ci_client = boto3.client("bedrock-agentcore", region_name=region_name)

    def _create_sessionid(self) -> str:
        try:
            session_response = self.ci_client.start_code_interpreter_session(
                codeInterpreterIdentifier="aws.codeinterpreter.v1",
                name="mcpInteractionSession",
                sessionTimeoutSeconds=900,
            )
        except ClientError as e:
            logging.error("***** Exception in create session %s", str(e))
            raise Exception(f"Failed to create session: {str(e)}")

        code_int_session_id = session_response["sessionId"]
        return code_int_session_id

    def _invoke_code_interpreter(
        self, operation: str, args: dict = None, code_int_session_id: str = ""
    ) -> CodeIntExecutionResult:
        start_time = time.time()
        try:
            if not code_int_session_id:
                code_int_session_id = self._create_sessionid()

            # Execute code
            response = self.ci_client.invoke_code_interpreter(
                codeInterpreterIdentifier="aws.codeinterpreter.v1",
                sessionId=code_int_session_id,
                name=operation,
                arguments=args if args else {},
            )
            output = ""
            for event in response["stream"]:
                output = json.dumps(event["result"], indent=2)

            execution_time = time.time() - start_time

            return CodeIntExecutionResult(
                output=output,
                code_int_session_id=code_int_session_id,
                execution_time=execution_time,
                success=True,
            )
        except ClientError as e:
            logging.error("***** Exception in code interpreter invocation %s", str(e))
            execution_time = time.time() - start_time
            return CodeIntExecutionResult(
                output="",
                code_int_session_id=code_int_session_id,
                error=str(e),
                execution_time=execution_time,
                success=False,
            )

    def execute_code(
        self, code: str, language: str = "python", code_int_session_id: str = ""
    ) -> CodeIntExecutionResult:
        args = {"code": code, "language": language, "clearContext": False}
        return self._invoke_code_interpreter("executeCode", args, code_int_session_id)

    def execute_command(self, command: str, code_int_session_id: str = "") -> CodeIntExecutionResult:
        args = {"command": command}
        return self._invoke_code_interpreter("executeCommand", args, code_int_session_id)

    def write_files(self, files: list, code_int_session_id: str = "") -> CodeIntExecutionResult:
        args = {"content": files}
        return self._invoke_code_interpreter("writeFiles", args, code_int_session_id)

    def read_files(self, paths: list, code_int_session_id: str = "") -> CodeIntExecutionResult:
        args = {"paths": paths}
        return self._invoke_code_interpreter("readFiles", args, code_int_session_id)
