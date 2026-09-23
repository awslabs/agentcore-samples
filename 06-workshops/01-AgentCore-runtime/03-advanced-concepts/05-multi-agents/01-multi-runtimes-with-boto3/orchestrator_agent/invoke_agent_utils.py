import boto3
import json


def invoke_agent_with_boto3(agent_arn, user_query):
    agentcore_client = boto3.client(
        "bedrock-agentcore",
    )
    print("Invoking agent...")
    boto3_response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=agent_arn,
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": user_query}),
    )

    if "text/event-stream" in boto3_response.get("contentType", ""):
        print("Processing streaming response...\n")
        result = ""
        for line in boto3_response["response"].iter_lines(chunk_size=1):
            if line:
                line = line.decode("utf-8")
                line = line[6:]
                if line.startswith('"') and line.endswith('"'):
                    line = line[1:-1]

                line = line.replace("\\n", "\n")
                print(line, end="", flush=True)
                result += line
    else:
        response_body = boto3_response["response"].read()
        response_data = json.loads(response_body)
        result = response_data

    return result
