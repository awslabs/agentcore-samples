import boto3
import json
import uuid
import time
from datetime import datetime, timedelta


def get_logs_by_trace_id(trace_id, log_group_names, region_name="us-east-1"):
    """
    Runs a CloudWatch Logs Insights query to find and print all logs for a given trace ID.

    Args:
        trace_id (str): The X-Ray trace ID to search for.
        log_group_names (list): A list of CloudWatch log group names to search.
        region_name (str): The AWS region where the log groups are located.
    """
    client = boto3.client("logs", region_name=region_name)

    query_string = f'fields @timestamp, @message | filter @message like /"{trace_id}"/ | sort @timestamp asc'
    out = []
    try:
        # Start the query
        response = client.start_query(
            logGroupNames=log_group_names,
            startTime=int(
                (time.time() - 3600) * 1000
            ),  # Search logs from the last hour
            endTime=int(time.time() * 1000),
            queryString=query_string,
            limit=10000,
        )
        query_id = response["queryId"]

        # Wait for the query to complete
        status = None
        while status not in ("Complete", "Failed", "Cancelled"):
            time.sleep(5)
            query_status_response = client.get_query_results(queryId=query_id)
            status = query_status_response["status"]
            # print(f'Current query status: {status}')

        # Retrieve and print the results
        if status == "Complete":
            results = query_status_response["results"]
            if not results:
                print(
                    f"No logs found for trace ID: {trace_id} - waiting 5 more seconds ..."
                )
            else:
                for log_event in results:
                    # Find the message field and print it
                    message = next(
                        (
                            field["value"]
                            for field in log_event
                            if field["field"] == "@message"
                        ),
                        None,
                    )
                    if message:
                        out.append(message)
        else:
            print(f"Query failed or was cancelled. Status: {status}")

    except Exception as e:
        print(f"An error occurred: {e}")
    return out


def list_agentcore_agents():
    client = boto3.client("bedrock-agentcore-control")
    if not client:
        return []
    try:
        # First, list all agents
        all_agents = client.list_agent_runtimes()["agentRuntimes"]
        return all_agents
    except Exception as e:
        print(f"Error listing Bedrock agents: {str(e)}")
        return []


def invoke_agentcore(agent_arn, agent_id, agent_version, prompt, session_id, trace_id):
    # Initialize the Bedrock AgentCore client
    agent_core_client = boto3.client("bedrock-agentcore")

    # Prepare the payload
    payload = json.dumps({"prompt": prompt}).encode()

    # Invoke the agent
    start_time = datetime.now()
    agent_response = agent_core_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        runtimeSessionId=session_id,
        traceId=trace_id,
        payload=payload,
    )
    latency = (datetime.now() - start_time).total_seconds()

    content = []
    for chunk in agent_response.get("response", []):
        content.append(chunk.decode("utf-8"))
    final_response = json.loads("".join(content))

    your_trace_id = trace_id.replace("Root=1-", "").split(";")[0].replace("-", "")
    your_log_groups = [f"/aws/bedrock-agentcore/runtimes/{agent_id}-{agent_version}"]

    trace = []
    timeout_minutes = 3
    check_interval_seconds = 5
    start_time = datetime.now()
    timeout_time = start_time + timedelta(minutes=timeout_minutes)

    while datetime.now() < timeout_time:
        trace = get_logs_by_trace_id(
            your_trace_id, your_log_groups, region_name="us-east-1"
        )
        if trace and len(trace) > 0:
            break
        time.sleep(check_interval_seconds)
    time.sleep(10)
    trace = get_logs_by_trace_id(
        your_trace_id, your_log_groups, region_name="us-east-1"
    )
    return {
        "agent_response": agent_response,
        "final_response": final_response,
        "latency": latency,
        "trace": trace,
    }


def generate_xray_trace_id():
    # Generate timestamp part (8 hex digits)
    timestamp = hex(int(time.time()))[2:].zfill(8)

    # Generate random part (24 hex digits)
    random_hex = uuid.uuid4().hex[:24]

    # Generate parent ID (16 hex digits)
    parent_id = uuid.uuid4().hex[:16]

    # Construct the trace ID (always sampled)
    trace_id = f"Root=1-{timestamp}-{random_hex};Parent={parent_id};Sampled=1"

    return trace_id


def extract_tools_info(log_entries):
    called_tools_list = []
    called_tools_args = []
    called_tools_ans = []

    # Parse each JSON entry
    for entry in log_entries:
        entry_data = json.loads(entry)

        # Check for tool calls (when the agent decides to use a tool)
        if "body" in entry_data and "content" in entry_data["body"]:
            content = entry_data["body"].get("content", "")

            # Check if this is a list with tool_use entries
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        tool_name = item.get("name")
                        tool_input = item.get("input", {})
                        # tool_id = item.get("id")

                        if tool_name:
                            called_tools_list.append(tool_name)
                            called_tools_args.append(tool_input)

        # Check for tool responses
        if (
            "body" in entry_data
            and "content" in entry_data["body"]
            and "id" in entry_data["body"]
        ):
            # tool_id = entry_data["body"].get("id")
            content = entry_data["body"].get("content")

            # If this is a tool response message
            if (
                "event.name" in entry_data.get("attributes", {})
                and entry_data["attributes"]["event.name"] == "gen_ai.tool.message"
            ):
                called_tools_ans.append(content)

    return called_tools_list, called_tools_args, called_tools_ans
