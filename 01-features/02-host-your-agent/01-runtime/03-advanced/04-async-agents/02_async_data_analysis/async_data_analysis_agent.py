# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import re
import os
import time
import json
import boto3
import logging
import threading
import tempfile
import html
import ast
import concurrent.futures
import atexit

from strands import Agent, tool
from strands.models import BedrockModel
from bedrock_agentcore.runtime import BedrockAgentCoreApp


def sanitize_task_id(task_id):
    # Only allow alphanumeric characters and hyphens
    if not re.match(r"^[a-zA-Z0-9\-]+$", str(task_id)):
        raise ValueError("Invalid task_id format")
    return str(task_id)


def validate_prompt_with_guardrails(prompt: str, region: str = "us-east-2") -> bool:
    """
    Validate user prompt using Amazon Bedrock Guardrails Standard Tier.

    Args:
        prompt: User input prompt to validate
        region: AWS region for Bedrock service

    Returns:
        bool: True if prompt is safe, False if blocked
    """
    try:
        import boto3

        bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)

        # Apply Bedrock Guardrails with Standard Tier for code domain protection
        guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID", "async-data-analysis-code-safety")

        response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion="DRAFT",
            source="INPUT",
            content=[{"text": {"text": prompt}}],
        )

        # Check if content was blocked by guardrails
        if response["action"] == "GUARDRAIL_INTERVENED":
            outputs = response.get("outputs", [])
            blocked_categories = []
            for output in outputs:
                if "type" in output:
                    blocked_categories.append(output["type"])

            logging.warning(f"Prompt blocked by Bedrock Guardrails: {blocked_categories}")
            return False

        logging.info("Prompt passed Bedrock Guardrails validation")
        return True

    except Exception as e:
        logging.warning(f"Bedrock Guardrails validation failed: {e}")
        # Fail open for legitimate analysis - don't block on service errors
        return True


def validate_generated_code_with_guardrails(code: str, region: str = "us-east-2") -> bool:
    """
    Validate generated code using Amazon Bedrock Guardrails Standard Tier.
    Specifically designed for code domain protection.

    Args:
        code: Python code to validate
        region: AWS region for Bedrock service

    Returns:
        bool: True if code is safe, False if dangerous
    """
    try:
        import boto3

        bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)

        # Apply Bedrock Guardrails to generated code
        guardrail_id = os.environ.get("BEDROCK_GUARDRAIL_ID", "async-data-analysis-code-safety")

        response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=guardrail_id,
            guardrailVersion="DRAFT",
            source="OUTPUT",  # Validating model output (generated code)
            content=[{"text": {"text": code}}],
        )

        # Check if code was blocked
        if response["action"] == "GUARDRAIL_INTERVENED":
            outputs = response.get("outputs", [])
            blocked_reasons = []
            for output in outputs:
                if "type" in output:
                    blocked_reasons.append(output["type"])

            logging.warning(f"Generated code blocked by Bedrock Guardrails: {blocked_reasons}")
            return False

        logging.info("Generated code passed Bedrock Guardrails validation")
        return True

    except Exception as e:
        logging.warning(f"Code validation with Bedrock Guardrails failed: {e}")
        # Fail open - allow execution if guardrails service unavailable
        return True


def validate_s3_bucket_access(bucket_name: str) -> bool:
    """
    Validate S3 bucket ownership and access permissions.

    Args:
        bucket_name: S3 bucket name to validate

    Returns:
        bool: True if bucket is accessible and owned by current account

    Raises:
        ValueError: If bucket access is denied or bucket doesn't exist
    """
    try:
        import boto3

        s3_client = boto3.client("s3")

        # Check bucket ownership by attempting to get bucket location
        s3_client.get_bucket_location(Bucket=bucket_name)

        # Verify we have necessary permissions
        s3_client.head_bucket(Bucket=bucket_name)

        return True
    except Exception as e:
        raise ValueError(f"S3 bucket access denied or bucket doesn't exist: {bucket_name}. Error: {e}")


os.environ["BYPASS_TOOL_CONSENT"] = "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler()],
)
logging.getLogger("strands.multiagent").setLevel(logging.DEBUG)

# Use thread pool with proper synchronization
# Make thread pool size configurable via environment variable
THREAD_POOL_SIZE = int(os.environ.get("ASYNC_TASK_THREAD_POOL_SIZE", "5"))
executor = concurrent.futures.ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
lock = threading.Lock()


# Register cleanup handler
def cleanup_executor():
    logging.info("Shutting down thread pool executor...")
    executor.shutdown(wait=True)
    logging.info("Thread pool executor shut down complete")


atexit.register(cleanup_executor)

# Code Security Validation
DANGEROUS_IMPORTS = {
    "os",
    "subprocess",
    "sys",
    "shutil",
    "glob",
    "pathlib",
    "socket",
    "urllib",
    "requests",
    "http",
    "ftplib",
    "smtplib",
    "eval",
    "exec",
    "__import__",
    "compile",
    "open",
}

ALLOWED_IMPORTS = {
    "pandas",
    "numpy",
    "matplotlib",
    "seaborn",
    "json",
    "math",
    "datetime",
    "time",
    "random",
    "statistics",
    "csv",
    "re",
}

DANGEROUS_PATTERNS = [
    r"eval\s*\(",
    r"exec\s*\(",
    r"__import__\s*\(",
    r"open\s*\(",
    r"file\s*\(",
    r"input\s*\(",
    r"raw_input\s*\(",
    r"compile\s*\(",
    r"getattr\s*\(",
    r"setattr\s*\(",
    r"delattr\s*\(",
    r"globals\s*\(",
    r"locals\s*\(",
    r"vars\s*\(",
    r"dir\s*\(",
]


def validate_generated_code(code: str) -> tuple[bool, str]:
    """Validate code for security issues."""
    try:
        # Check for dangerous patterns
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                return False, f"Dangerous function detected: {pattern}"

        # Parse AST to check imports
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in DANGEROUS_IMPORTS:
                            return False, f"Dangerous import blocked: {alias.name}"
                        if alias.name not in ALLOWED_IMPORTS:
                            return False, f"Import not in whitelist: {alias.name}"

                elif isinstance(node, ast.ImportFrom):
                    if node.module in DANGEROUS_IMPORTS:
                        return False, f"Dangerous module import blocked: {node.module}"
                    if node.module and node.module not in ALLOWED_IMPORTS:
                        return False, f"Module not in whitelist: {node.module}"

        except SyntaxError:
            return False, "Code contains syntax errors"

        return True, ""

    except Exception as e:
        logging.error(f"Code validation error: {e}")
        return False, f"Validation failed: {str(e)}"


# Initialize models
sonnet = BedrockModel(model_id="us.anthropic.claude-sonnet-4-20250514-v1:0")

haiku = BedrockModel(model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0")


class CodeInterpreterClient:
    """Direct boto3 client for AWS Bedrock AgentCore CodeInterpreter"""

    def __init__(self, region="us-east-1"):
        self.client = boto3.client(
            "bedrock-agentcore",
            region_name=region,
            endpoint_url=f"https://bedrock-agentcore.{region}.amazonaws.com",
        )
        self.session_id = None
        self._start_session()

    def _start_session(self):
        """Start a new CodeInterpreter session"""
        try:
            response = self.client.start_code_interpreter_session(
                codeInterpreterIdentifier="aws.codeinterpreter.v1",
                name=f"analysis-session-{int(time.time())}",
                sessionTimeoutSeconds=3600,
            )
            self.session_id = response["sessionId"]
            logging.info(f"Started CodeInterpreter session: {self.session_id}")
        except Exception as e:
            logging.error(f"Failed to start CodeInterpreter session: {e}")
            raise

    def execute_code(self, code: str) -> str:
        """Execute Python code and return results"""
        if not self.session_id:
            raise RuntimeError("No active session")

        response = self.client.invoke_code_interpreter(
            codeInterpreterIdentifier="aws.codeinterpreter.v1",
            sessionId=self.session_id,
            name="executeCode",
            arguments={"language": "python", "code": code},
        )

        # Process the event stream response
        result_text = ""
        for event in response.get("stream", []):
            if "result" in event:
                result = event["result"]
                if "content" in result:
                    for content_item in result["content"]:
                        if content_item["type"] == "text":
                            result_text += content_item["text"]

        return result_text or str(response)


app = BedrockAgentCoreApp()


# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

CODING_AGENT_SYSTEM_PROMPT = """
You are a Python code generator. Generate ONLY executable Python code with NO
markdown formatting.

CRITICAL RULES:
- Output raw Python code ONLY - no ```python blocks, no explanations, no
  comments outside the code
- The data file 'data.csv' is ALREADY AVAILABLE in the current directory -
  DO NOT try to download it from S3
- ALWAYS start by reading the data: df = pd.read_csv('data.csv')
- DO NOT use boto3 or try to access S3 - the data is already local
- Use print() statements to output all results, analysis, and reports
- Import all required libraries (pandas, numpy, etc.) at the top of your code
- Format output clearly with labels and structure using print statements

EXAMPLE OUTPUT FORMAT:
import pandas as pd
import numpy as np

# Read the data that's already available
df = pd.read_csv('data.csv')

print("Dataset Overview:")
print(f"Rows: {len(df)}, Columns: {len(df.columns)}")
print()

print("Analysis Results:")
result = df.groupby('product')['price'].mean()
print(result.to_string())

IMPORTANT: DO NOT import boto3 or try to read from S3. The data is already
in 'data.csv'.
"""

PRIMARY_AGENT_SYSTEM_PROMPT = """
You are a helpful assistant. You receive a request from the user and answer  and answer immediately
if it is a generic question, or route to a background function async_analysis_task.
If you route to the background function, reply to the user mentioning that the task is running and you can answer other questions, do not wait for the results.
Tell the user the task id when routing to background function. While tasks run, you remain available.
to help the user. You can check task status and retrieve results when user asks for it.
"""

# ============================================================================
# S3 UTILITY FUNCTIONS
# ============================================================================


def extract_s3_uri_from_text(text: str) -> str:
    """
    Extract S3 URI from text using regex.

    Args:
        text: Text that may contain an S3 URI

    Returns:
        str: S3 URI if found, None otherwise
    """
    if not text:
        return None

    # Match S3 URI: s3://bucket/path/file.ext
    # Captures bucket name, path, and file with extension
    pattern = r"s3://[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_/]+\.[a-zA-Z0-9]+"
    match = re.search(pattern, text)

    return match.group(0) if match else None


def parse_s3_uri(s3_uri: str) -> tuple:
    """
    Parse an S3 URI into bucket and key components.

    Args:
        s3_uri: S3 URI in format s3://bucket-name/path/to/file

    Returns:
        tuple: (bucket, key) or (None, None) if invalid
    """
    if not s3_uri or not s3_uri.startswith("s3://"):
        return None, None

    s3_path = s3_uri[5:]  # Remove 's3://'
    parts = s3_path.split("/", 1)

    if len(parts) < 2:
        return None, None

    bucket = parts[0]
    key = parts[1]

    return bucket, key


def build_s3_output_uri(s3_input_uri: str, task_id: str) -> str:
    """
    Build an S3 output URI based on input URI and task ID.
    Uses the same bucket and directory as input, but changes filename.

    Args:
        s3_input_uri: Input S3 URI
        task_id: Task identifier

    Returns:
        str: Output S3 URI with task result filename
    """
    if not s3_input_uri:
        return None

    bucket, key = parse_s3_uri(s3_input_uri)
    if not bucket or not key:
        return None

    safe_task_id = sanitize_task_id(task_id)
    safe_bucket = html.escape(str(bucket))

    # Get the directory path from the input key
    path_parts = key.rsplit("/", 1)
    if len(path_parts) > 1:
        output_prefix = path_parts[0]
        safe_output_prefix = html.escape(str(output_prefix))
        return f"s3://{safe_bucket}/{safe_output_prefix}/task_{safe_task_id}_result.json"
    else:
        return f"s3://{safe_bucket}/task_{safe_task_id}_result.json"


def upload_to_s3(local_file: str, s3_uri: str, task_id: str = None) -> bool:
    """
    Upload a local file to S3.

    Args:
        local_file: Path to local file
        s3_uri: S3 URI destination
        task_id: Optional task ID for logging

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        import boto3

        bucket, key = parse_s3_uri(s3_uri)
        if not bucket or not key:
            log_prefix = f"[BACKGROUND] Task {task_id} - " if task_id else ""
            logging.warning(f"{log_prefix}Invalid S3 URI format: {s3_uri}")
            return False

        # Validate bucket access before upload
        validate_s3_bucket_access(bucket)

        s3_client = boto3.client("s3")
        s3_client.upload_file(local_file, bucket, key)

        log_prefix = f"[BACKGROUND] Task {task_id} - " if task_id else ""
        logging.info(f"{log_prefix}Successfully uploaded to S3: {s3_uri}")
        return True

    except Exception as e:
        log_prefix = f"[BACKGROUND] Task {task_id} - " if task_id else ""
        logging.error(f"{log_prefix}S3 upload failed: {e}")
        return False


@tool(
    name="async_analysis_task",
    description=(
        "Execute Python code asynchronously for data analysis tasks. "
        "Automatically detects S3 URIs in the request, downloads data, "
        "executes code in Code Interpreter, and saves results back to S3 "
        "in the same location."
    ),
)
def async_analysis_task(request: str):
    """
    Write and execute Python code asynchronously for data analysis tasks.

    This tool:
    1. Automatically detects S3 URIs in your request
    2. Downloads the data and loads it into a Code Interpreter session as 'data.csv'
    3. Generates Python code based on your request
    4. Executes the code in an isolated Code Interpreter environment
    5. Saves results locally and to S3 (same bucket/path as input, different filename)

    Args:
        request: A clear description of the data analysis task. Include the S3 URI of your data
                 if you want to load and analyze it. Be specific about what analysis to perform.

                 Examples:
                 - "Load data from s3://my-bucket/data/sales.csv and calculate average price by product"
                 - "Using s3://my-bucket/reports/data.csv, find the top 5 products by revenue"
                 - "Write code that generates the first 20 prime numbers" (no S3 data needed)

    Returns:
        str: Confirmation message with task ID and S3 output location (if applicable)

    Notes:
        - If an S3 URI is detected, results are automatically saved to the same S3 location
          with filename: task_{task_id}_result.json
        - Results are always saved locally to /tmp/task_{task_id}_result.json
        - Use get_task_results(task_id) to retrieve completed results
    """

    # Extract S3 input URI from request if present
    s3_input_uri = extract_s3_uri_from_text(request)
    if s3_input_uri:
        logging.info(f"[ASYNC_TASK] Detected S3 input URI: {s3_input_uri}")

    logging.info(f"[ASYNC_TASK] Starting async task for request: {request}")

    # Implement proper thread safety with lock
    with lock:
        task_id = app.add_async_task("async_analysis_task")
        logging.info(f"[ASYNC_TASK] Created task with ID: {task_id}")

    # Build S3 output URI using the same path as input (just change filename)
    s3_output_uri = build_s3_output_uri(s3_input_uri, str(task_id))
    if s3_output_uri:
        logging.info(f"[ASYNC_TASK] Will save results to: {s3_output_uri}")

    # Submit to thread pool instead of creating raw threads
    try:
        executor.submit(_run_async_analysis_task, request, task_id, s3_input_uri, s3_output_uri)
        logging.info(f"[ASYNC_TASK] Task {task_id} submitted to thread pool")
    except Exception as e:
        logging.error(f"[ASYNC_TASK] Failed to submit task {task_id} to thread pool: {e}")
        raise

    response = f"Code writing started in the background. Task ID: {task_id}. Results will be available in the future."
    if s3_output_uri:
        response += f" Results will also be saved to {s3_output_uri}"
    return response


def _extract_text_from_stream(response) -> str:
    """Helper to extract text content from Code Interpreter streaming response."""
    output_parts = []
    error_parts = []

    for event in response.get("stream", []):
        if "result" in event:
            result = event["result"]
            if "content" in result:
                for content_item in result["content"]:
                    if content_item.get("type") == "text":
                        output_parts.append(content_item["text"])

        # Also capture error events
        if "error" in event:
            error_parts.append(str(event["error"]))

    # Combine output and errors
    all_output = "\n".join(output_parts)
    if error_parts:
        all_output += "\n\nERRORS:\n" + "\n".join(error_parts)

    return all_output


def _has_execution_error(result: str) -> bool:
    """Check if code execution result contains errors."""
    error_indicators = ("Traceback", "Error", "Exception")
    return any(indicator in result for indicator in error_indicators)


def _has_execution_error(result: str) -> bool:
    """Check if execution result contains error indicators."""
    error_indicators = [
        "Traceback",
        "Error:",
        "Exception:",
        "SyntaxError",
        "NameError",
        "TypeError",
    ]
    return any(indicator in str(result) for indicator in error_indicators)


def _build_retry_prompt(request: str, error_context: str) -> str:
    """Build a detailed retry prompt with error context."""
    return (
        f"Your previous code failed with this error:\n\n"
        f"ERROR OUTPUT:\n{error_context}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Carefully read the error message above\n"
        f"2. Identify what went wrong (missing import, wrong column name, syntax error, etc.)\n"
        f"3. Fix the specific issue\n"
        f"4. Generate corrected Python code\n\n"
        f"ORIGINAL REQUEST: {request}\n\n"
        f"Generate the FIXED Python code now:"
    )


def _save_task_result(task_id: str, data: dict, s3_output_uri: str = None) -> str:
    """Save task result to local file and optionally upload to S3."""
    temp_dir = tempfile.gettempdir()
    safe_task_id = sanitize_task_id(task_id)
    local_file = os.path.join(temp_dir, f"task_{safe_task_id}_result.json")
    with open(local_file, "w") as f:
        json.dump(data, f, indent=2)

    if s3_output_uri:
        logging.info(f"[BACKGROUND] Task {task_id} - Uploading result to S3: {s3_output_uri}")
        if upload_to_s3(local_file, s3_output_uri, task_id):
            data["s3_uri"] = s3_output_uri

    return local_file


def _download_s3_data(task_id: str, s3_input_uri: str, code_client) -> None:
    """Download data from S3 and write to Code Interpreter session."""
    logging.info(f"[BACKGROUND] Task {task_id} - Downloading data from S3: {s3_input_uri}")
    bucket, key = parse_s3_uri(s3_input_uri)

    if not bucket or not key:
        raise ValueError(f"Invalid S3 URI: {s3_input_uri}")

    # Validate bucket access before download
    validate_s3_bucket_access(bucket)

    s3_client = boto3.client("s3")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    csv_content = response["Body"].read().decode("utf-8")

    logging.info(f"[BACKGROUND] Task {task_id} - Writing data to Code Interpreter session")

    # Write the CSV data using code execution
    write_code = f'''
import os
with open("data.csv", "w") as f:
    f.write("""{csv_content}""")
print("Data file written successfully")
'''

    result = code_client.execute_code(write_code)
    logging.info(f"[BACKGROUND] Task {task_id} - Data write result: {result}")


def _execute_with_retry(task_id: str, request: str, coding_agent, code_client, max_retries: int = 3):
    """Execute code generation and execution with retry logic."""
    error_context = ""
    region = os.environ.get("AWS_REGION", "us-east-2")

    # First validate the user request with Bedrock Guardrails
    if not validate_prompt_with_guardrails(request, region):
        raise Exception("Request blocked by security guardrails - potential code injection detected")

    for attempt in range(max_retries):
        # Build prompt based on attempt number
        if attempt == 0:
            prompt = f"Write Python code to accomplish the following: {request}"
        else:
            prompt = _build_retry_prompt(request, error_context)
            safe_prompt = html.escape(str(prompt)[:300])
            logging.info(f"[BACKGROUND] Task {task_id} - Retry prompt preview: {safe_prompt}...")

        # Generate code
        logging.info(f"[BACKGROUND] Task {task_id} - Attempt {attempt + 1}/{max_retries}: Calling coding agent")
        coding_agent_response = coding_agent(prompt)
        python_code = coding_agent_response.message["content"][0]["text"]
        logging.info(f"[BACKGROUND] Task {task_id} - Code generated, length: {len(python_code)} chars")

        # Validate generated code with Bedrock Guardrails
        if not validate_generated_code_with_guardrails(python_code, region):
            error_context = "Generated code blocked by Bedrock Guardrails for security violations"
            logging.warning(f"[BACKGROUND] Task {task_id} - Code blocked by Bedrock Guardrails")

            if attempt < max_retries - 1:
                logging.info(f"[BACKGROUND] Task {task_id} - Retrying with security feedback...")
                continue
            else:
                raise Exception("Unable to generate safe code after maximum retries - blocked by Bedrock Guardrails")

        # Execute code if validation passes
        try:
            logging.info(f"[BACKGROUND] Task {task_id} - Executing code in secure environment")

            # Use the new CodeInterpreter client
            result = code_client.execute_code(python_code)

            logging.info(f"[BACKGROUND] Task {task_id} - Execution completed successfully")

            # Check for execution errors
            if _has_execution_error(result):
                error_context = f"Execution error: {result}"
                logging.warning(f"[BACKGROUND] Task {task_id} - Execution failed: {result}")

                if attempt < max_retries - 1:
                    continue
                else:
                    return (
                        python_code,
                        f"Code execution failed after {max_retries} attempts: {result}",
                    )
            else:
                logging.info(f"[BACKGROUND] Task {task_id} - Code executed successfully")
                return python_code, result

        except Exception as e:
            error_context = f"Execution exception: {str(e)}"
            logging.error(f"[BACKGROUND] Task {task_id} - Execution exception: {e}")

            if attempt < max_retries - 1:
                continue
            else:
                return python_code, f"Code execution failed with exception: {str(e)}"

    return "", "Failed to generate and execute code after maximum retries"


def _mark_task_failed(task_id: str, s3_output_uri: str, error: Exception) -> None:
    """Mark task as failed and save error details."""
    import traceback

    error_trace = traceback.format_exc()
    logging.error(f"[BACKGROUND] Task {task_id} - ERROR: {error}")
    logging.error(error_trace)

    error_data = {
        "status": "failed",
        "error": str(error),
        "traceback": error_trace,
        "task_id": task_id,
    }

    _save_task_result(task_id, error_data, s3_output_uri)

    # Mark task as failed in AgentCore
    logging.info(f"[BACKGROUND] Task {task_id} - Marking task as failed in AgentCore")
    try:
        app.fail_async_task(task_id)
    except AttributeError:
        logging.warning(f"[BACKGROUND] Task {task_id} - fail_async_task method not available")
    except Exception as fail_error:
        logging.error(f"[BACKGROUND] Task {task_id} - Error marking as failed: {fail_error}")


def _run_async_analysis_task(request: str, task_id: str, s3_input_uri: str = None, s3_output_uri: str = None):
    """Execute async analysis task with code generation and execution."""
    logging.info(f"[BACKGROUND] Task {task_id} - Starting execution")
    code_client = None

    try:
        # Initialize coding agent
        logging.info(f"[BACKGROUND] Task {task_id} - Creating coding agent")
        coding_agent = Agent(name="coding_agent", system_prompt=CODING_AGENT_SYSTEM_PROMPT, model=haiku)

        # Initialize Secure Code Interpreter with network isolation
        logging.info(f"[BACKGROUND] Task {task_id} - Initializing Secure Code Interpreter")
        region = os.environ.get("AWS_REGION", "us-west-2")

        # Extract allowed S3 buckets from input URI
        allowed_buckets = []
        if s3_input_uri:
            bucket, _ = parse_s3_uri(s3_input_uri)
            if bucket:
                allowed_buckets.append(bucket)

        # Use direct boto3 CodeInterpreter client
        try:
            code_client = CodeInterpreterClient(region=region)
            logging.info(f"[BACKGROUND] Task {task_id} - CodeInterpreter session started: {code_client.session_id}")

        except Exception as e:
            logging.error(f"[BACKGROUND] Task {task_id} - CodeInterpreter initialization failed: {e}")
            raise Exception(
                f"CodeInterpreter service unavailable. This service may be in preview and require AWS support to enable. Error: {e}"
            )

        # Download and write data file if S3 input URI provided
        if s3_input_uri:
            _download_s3_data(task_id, s3_input_uri, code_client)

        # Execute with retry logic
        python_code, result = _execute_with_retry(task_id, request, coding_agent, code_client)

        # Store result
        result_data = {
            "status": "completed",
            "task_id": task_id,
            "code": python_code,
            "result": result,
            "s3_input_uri": s3_input_uri,
        }

        _save_task_result(task_id, result_data, s3_output_uri)

        # Mark task as complete in AgentCore
        logging.info(f"[BACKGROUND] Task {task_id} - Marking task as complete in AgentCore")
        app.complete_async_task(task_id)
        logging.info(f"[BACKGROUND] Task {task_id} - Successfully completed")

    except Exception as e:
        _mark_task_failed(task_id, s3_output_uri, e)

    finally:
        # Always stop the Code Interpreter session
        if code_client:
            try:
                code_client.stop()
                logging.info(f"[BACKGROUND] Task {task_id} - Code Interpreter session stopped")
            except Exception as e:
                logging.error(f"[BACKGROUND] Task {task_id} - Error stopping Code Interpreter: {e}")


@tool(
    name="get_task_results",
    description=(
        "Retrieve the results of a completed async analysis task using its task ID. "
        "Returns the generated code, analysis results, and status. "
        "call get_task_status function to check the status of the running task and only call this function after getting the confirmation that task is completed"
    ),
)
def get_task_results(task_id: str):
    """
    Get results of a completed task from file system.

    This function retrieves the results of an asynchronous task that was previously
    started using async_analysis_task. Results are stored as JSON files in the
    temporary directory with the naming pattern: task_{task_id}_result.json
    Args:
        task_id (str): The unique identifier of the task whose results to retrieve.
                      This ID is returned when starting a task with async_analysis_task.

    Returns:
        dict: A dictionary containing the task results.

    Notes:
        - Tasks that are still running will return a "not_found" status
        - Results files are stored locally in temp directory and may be cleaned up by the system
        - For tasks that processed S3 data, results may also be available in S3
    """
    import json
    import tempfile

    try:
        temp_dir = tempfile.gettempdir()
        safe_task_id = sanitize_task_id(task_id)
        result_file = os.path.join(temp_dir, f"task_{safe_task_id}_result.json")
        with open(result_file, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "status": "not_found",
            "message": f"No results found for task {task_id}. Task may still be running or hasn't started yet.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Error reading results: {str(e)}"}


@tool(name="get_task_status", description=("Get the status of the running tasks"))
def get_task_status():
    """Get status of running tasks"""
    # Get task info
    task_info = app.get_async_task_info()
    logging.debug(task_info)

    tasks_result = {
        "message": "Current task information",
        "task_info": task_info,
    }
    return tasks_result


# Build primary agent
primary_agent = Agent(
    name="primary_agent",
    system_prompt=PRIMARY_AGENT_SYSTEM_PROMPT,
    tools=[async_analysis_task, get_task_results, get_task_status],
    model=sonnet,
)


@app.entrypoint
def handler(payload, context):
    result = primary_agent(payload.get("prompt"))
    return {"result": result.message}


# Run the application
if __name__ == "__main__":
    app.run()
