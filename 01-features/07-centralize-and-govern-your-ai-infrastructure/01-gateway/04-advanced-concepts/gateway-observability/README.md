# Configure observability for AgentCore gateway with Amazon CloudWatch and AWS CloudTrail

> [!CAUTION]
> This sample is currently in read-only mode. Executable version of this sample is currently being worked on. 

- Amazon CloudWatch focuses on real-time performance monitoring and operational troubleshooting for AgentCore gateway, providing detailed metrics and logs for latency, error rates, and usage patterns.
- AWS CloudTrail focuses on security, compliance, and auditing by recording a full history of API calls and user actions related to the gateway.

Together, they offer a holistic observability and governance framework for managing AgentCore gateway in production.

**Amazon CloudWatch**

Primarily logs AgentCore gateway Data Plane interactions - List gateway tools (tools/list), Call a gateway tool (tools/call), Search for a gateway tool (tools/call)

| Component Type          | Description                      |
| ----------------------- | -------------------------------- |
| Metrics                 | Performance and operational data |
| Traces, Spans, Requests | Request trajectory tracking      |
| Application Logs        | Data plane operational logs      |

**Amazon CloudTrail**

Logs AgentCore gateway Control Plane (CreateGateway, ListGateway, DeleteGateaway etc.) as well as Data Plane interactions (InvokeGateway etc.)

| Component Type    | Description                                                                                                                                                                         |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Management Events | Contains identity information for control plane requests, enabled by default when trail is created                                                                                  |
| Data Events       | Information about the resource operations performed on or in a resource . Data events are often high-volume activities. Needs to be explicity enabled. Additional charges incurred. |

## Tutorial Details

| Information          | Details                                                             |
| :------------------- | :------------------------------------------------------------------ |
| Tutorial type        | Interactive                                                         |
| AgentCore components | AgentCore gateway, AgentCore runtime, AgentCore identity            |
| Agentic Framework    | Strands Agents                                                      |
| gateway Target type  | AWS Lambda                                                          |
| Inbound Auth         | OAuth (Cognito)                                                     |
| Outbound Auth        | AWS IAM                                                             |
| Tutorial components  | Amazon CloudWatch, AWS CloudTrail, Amazon Bedrock AgentCore gateway |
| Tutorial vertical    | Cross-vertical                                                      |
| Example complexity   | Intermediate                                                        |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Bedrock model access for `global.anthropic.claude-haiku-4-5-20251001-v1:0` (if using Strands demo)

## Deployment Steps

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy the Lambda Function

Deploy the sample Lambda function that will be exposed as MCP tools through the gateway. This function contains two operations: `get_order` and `update_order`.

```bash
cd ../../gatewaylabproject/

uv run python scripts/observability/deploy_lambda.py
```

Capture the Lambda function ARN:

```bash
LAMBDA_ARN=$(aws lambda get-function \
  --function-name observability-gateway-lambda \
  --query 'Configuration.FunctionArn' --output text)

echo "Lambda ARN: $LAMBDA_ARN"
```

### Step 3: Create AgentCore gateway (AgentCore CLI)

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
agentcore add gateway \
  --name observability-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'observability-gateway'))
")
echo "gateway ID: $GATEWAY_ID"

GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp
echo "gateway URL: $GATEWAY_URL"

GATEWAY_ARN=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayArn' --output text)
echo "gateway ARN: $GATEWAY_ARN"

GATEWAY_NAME=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'name' --output text)
echo "gateway Name: $GATEWAY_NAME"
```

### Step 4: Create the Lambda Target (AgentCore CLI)

Attach the Lambda function as a gateway target:

```bash
agentcore add gateway-target \
  --name observability-lambda-target \
  --type lambda-function-arn \
  --lambda-arn $LAMBDA_ARN \
  --gateway observability-gateway

agentcore deploy --yes
```

### Step 5: Verify Deployment

```bash
agentcore status
```

Ensure all resources are in `READY` state.

---

## Configuring AgentCore gateway observability with Amazon CloudWatch

### Configuring AgentCore gateway Application Logs

Scenarios where error logging will show in AgentCore gateway application logs:

- MCP Request tools/call for a gateway where the execution role does not trust bedrock-agentcore
- MCP Request tools/call on a gateway where execution role does not have correct permissions to the CredentialProviderArn associated to a target
- MCP Request tools/call on a gateway where execution role does not have correct permissions to invoke the lambda function target
- MCP Request has missing authorization header
- MCP Request has an invalid bearer token invalid (e.i expired, invalid, client id not allowed)
- MCP Request tools/call to a tool that does not exist

#### Step 0: Create new log group for vended log delivery

**Note down the CloudWatch log group name**

```python
import boto3
import os

REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')

## Initialize CloudWatch Logs client
logs_client = boto3.client('logs', region_name=REGION)
sts_client = boto3.client('sts', region_name=REGION)

## Use the GATEWAY_ID environment variable or replace with your gateway ID
gateway_id = os.environ.get('GATEWAY_ID', '<your-gateway-id>')

## Define log group name
log_group_name = '/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/' + gateway_id
## Get AWS account ID
account_id = sts_client.get_caller_identity()['Account']
log_group_arn = f"arn:aws:logs:{REGION}:{account_id}:log-group:{log_group_name}:*"
print(f"Log Group ARN (constructed): {log_group_arn}")

try:
    # Create log group
    logs_client.create_log_group(logGroupName=log_group_name)
    print(f"NOTE DOWN: Successfully created log group: {log_group_name}")
    cloudwatch_log_group = log_group_name

except logs_client.exceptions.ResourceAlreadyExistsException:
    print(f"Log group {log_group_name} already exists")
    cloudwatch_log_group = log_group_name
except Exception as e:
    print(f"Error creating log group: {e}")

```

## Step 1: Create delivery source for logs

```python
## Use PutDeliverySource to create a delivery source, which is a logical object that represents the resource that is actually sending the logs
## Use the GATEWAY_ARN and GATEWAY_NAME environment variables or replace with your values
gateway_arn = os.environ.get('GATEWAY_ARN', '<your-gateway-arn>')
gateway_name = os.environ.get('GATEWAY_NAME', '<your-gateway-name>')

delivery_source_response = logs_client.put_delivery_source(
            name = f"{gateway_name}-logs-source",
            logType='APPLICATION_LOGS',
            resourceArn=gateway_arn
        )
```

## Step 2: Create delivery destinations

```python
## A delivery destination can represent a log group in CloudWatch Logs, an Amazon S3 bucket, a delivery stream in Firehose, or X-Ray. Here we are using CloudWatch log group

delivery_destination_response = logs_client.put_delivery_destination(
            name='bedrock-agentcore-gw-destination',
            deliveryDestinationType='CWL',
            deliveryDestinationConfiguration = {
              "destinationResourceArn": log_group_arn
           },
            outputFormat= "json",
        )
print(delivery_destination_response['deliveryDestination']['arn'])
```

## Step 3: Create logs delivery (connect sources to destinations)

```python
## Create a logs delivery by pairing the source and destination. A delivery is a connection between a logical delivery source and a logical delivery destination that you have already created
delivery_response = logs_client.create_delivery(
            deliverySourceName=f"{gateway_name}-logs-source",
            deliveryDestinationArn=delivery_destination_response['deliveryDestination']['arn'],
            recordFields=['resource_arn','event_timestamp','body','account_id','timestamp','trace_id','span_id','request_id','gateway_id'],
)
```

```python
import time
time.sleep(10)
```

## Step 4: Check AWS Console

- Head to [Amazon Bedrock AgentCore](https://console.aws.amazon.com/bedrock-agentcore/) service on AWS Console.
- Ensure that the AWS region is correct.
- Select **Gateways**.
- Select the gateway you created.
- Check **Log deliveries and tracing** to see an entry

![Log deliveries](images/24-amazon-bedrock-agentcore-gw.png)

### Vended Logs: Invoking tools/list operation on AgentCore gateway

#### Obtaining token for inbound gateway Authentication

```python
import base64
import requests

## Use the environment variables captured during deployment
gateway_client_id = os.environ.get('GATEWAY_CLIENT_ID', '<your-gateway-client-id>')
gateway_client_secret = os.environ.get('GATEWAY_CLIENT_SECRET', '<your-gateway-client-secret>')
token_endpoint = os.environ.get('TOKEN_ENDPOINT', '<your-token-endpoint>')

auth_string = f"{gateway_client_id}:{gateway_client_secret}"
auth_b64 = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')

token_response = requests.post(
    token_endpoint,
    headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f'Basic {auth_b64}'
    },
    data={
        'grant_type': 'client_credentials',
        'scope': 'api/gateway'
    }
)

token = token_response.json()["access_token"]
print("Token obtained successfully")
```

## Enable Live Tailing in Amazon CloudWatch

```python
print(cloudwatch_log_group)
```

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)**
- Navigate to **Log Groups**
- Select specific log group from above output: for example **/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/gatewayID**
- Click on **Start tailing**

<img src="images/2-cloudwatch-live-tail.png" alt="2-cloudwatch-live-tail" width="60%">

### Invoking tools/list on AgentCore gateway

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

gateway_url = os.environ.get('GATEWAY_URL', '<your-gateway-url>')

def create_streamable_http_transport():
    return streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"})

client = MCPClient(create_streamable_http_transport)

with client:
    # Retrieve all tool capabilities from the gateway
    tools = client.list_tools_sync()
    for tool in tools:
        print(tool.tool_name)
```

#### What happens in the backend

**There will be 8 log messages corresponding to the operation above with 3 Trace IDs, 3 Request IDs and 3 Span IDs.**

**Flow Sequence from the Vended logs showing details such as `trace_id`, `span_id`, `request_id`.**<br/>
**You can also see the `gateway_id`.**

**Trace ID:** Represents the overall transaction or agent workflow—covering the full flow of processing a user's conversation, orchestration, and tool invocations

**Request ID:** Identifies a specific request made to the AgentCore gateway within the context of a trace. Multiple request IDs can exist within a trace, reflecting different API calls or gateway events during the transaction.

**Span ID:** Marks a specific action or operation performed for a particular request. Each request may generate several spans, each with its own span ID, representing granular steps such as tool calls or memory events within the parent request

**Sequence showing MCP handshake, list tools request:**

There are 3 trace ids with 3 request and span ids:

<font color="blue">Trace ID: 1 -> MCP Handshake <br/></font>
<font color="purple">Trace ID: 2 -> Readiness after Handshake - notifications/initialized <br/></font>
<font color="green">Trace ID: 3 -> Tools/list Request / Response<br/></font>

![overall_sequence](images/20-traceids-sequence.png)

**Trace ID: 68eb191773c47c5f6babe5cb4f81bd69**

- <font color="blue">Client → gateway: "Hi, I'm client MCP v0.1.0, can we talk using protocol version 2025-06-18"</font> <br/><br/>
  <img src="images/3-sequence1.png" alt="3-sequence1" width="60%">
- <font color="blue">gateway → Client: "Yes, I can talk to you."</font> <br/><br/>
  <img src="images/4-sequence2.png" alt="4-sequence2" width="60%">
- <font color="blue"> gateway → Client: "This is my info" </font> <br/><br/>
  <img src="images/5-sequence3.png" alt="5-sequence3" width="60%">

**Trace ID: 68eb1917268b63ef1290ed317254df8c**

- <font color="purple">Client → gateway: "After successful initialization, the client sends a notification to indicate it's ready:"</font> <br/><br/>
  <img src="images/6-sequence4.png" alt="6-sequence4" width="60%">
- <font color="purple">gateway → Client: "Received notification about system being ready"</font> <br/><br/>
  <img src="images/7-sequence5.png" alt="7-sequence5" width="60%">

**Trace ID: 68eb19177631f5357c06be8c182a99df**

- <font color="green">Client → gateway: "Calling list/tools"</font> <br/><br/>
  <img src="images/8-sequence6.png" alt="8-sequence6" width="60%">
- <font color="green">gateway → Client: "Received tools list request"</font> <br/><br/>
  <img src="images/9-sequence7.png" alt="9-sequence7" width="60%">
- <font color="green">gateway → Client: "Here are your tools: get_order_tool and update_order_tool"</font> (Complete Response)<br/><br/>
  <img src="images/10-sequence8.png" alt="10-sequence8" width="60%">

### Configure tracing delivery to CloudWatch using the console

This section describes how to enable trace delivery to CloudWatch to track the flow of interactions through your application allowing you to visualize requests, identify performance bottlenecks, troubleshoot errors, and optimize performance.

#### Step 1: Create delivery source for traces

```python
traces_source_response = logs_client.put_delivery_source(
    name=f"{gateway_name}-traces-source",
    logType="TRACES",
    resourceArn=gateway_arn
)
```

#### Step 2: Create delivery destinations

```python
## A delivery destination can represent a log group in CloudWatch Logs, an Amazon S3 bucket, a delivery stream in Firehose, or X-Ray. Here we are using CloudWatch log group

traces_destination_response = logs_client.put_delivery_destination(
            name=f"{gateway_name}-traces-destination",
            deliveryDestinationType='XRAY'
        )
print(traces_destination_response['deliveryDestination']['arn'])
```

## Step 3: Create traces delivery (connect sources to destinations)

```python
## Create a logs delivery by pairing the source and destination. A delivery is a connection between a logical delivery source and a logical delivery destination that you have already created
delivery_response = logs_client.create_delivery(
            deliverySourceName=f"{gateway_name}-traces-source",
            deliveryDestinationArn=traces_destination_response['deliveryDestination']['arn']
)
```

```python
import time
time.sleep(10)
```

## Step 4: Check AWS Console

- Head to [Amazon Bedrock AgentCore](https://console.aws.amazon.com/bedrock-agentcore/) service on AWS Console.
- Ensure that the AWS region is correct.
- Select **Gateways**.
- Select the gateway you created.
- Check **Tracing** to verify that it is `Enabled`

![enable-tracing](images/26-enable-tracing.png)

### Traces in GenAI observability Dashboard - gateway Level

#### Invoking tools/list operation on AgentCore gateway

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

def create_streamable_http_transport():
    return streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"})

client = MCPClient(create_streamable_http_transport)

## Define specific tool parameters
TOOL_NAME = "observability-lambda-target___get_order_tool"
ORDER_ID = "123"

with client:
    try:
        # Call the get_order_tool
        result = client.call_tool_sync(
            tool_use_id="get-order-id-123-call-1",
            name=TOOL_NAME,
            arguments={"orderId": ORDER_ID}
        )

        # Print the tool's response
        print(f"\nGet Order Tool Response for Order ID {ORDER_ID}:")
        print(f"Content: {result['content'][0]['text']}")

    except Exception as e:
        print(f"Error occurred while calling {TOOL_NAME}: {str(e)}")
```

## Amazon CloudWatch gateway Traces and Spans

> [!NOTE]
> It takes at least 2-5 minutes for the gateway and Traces to be reflected in the GenAI observability Dashboard below.

```python
print(cloudwatch_log_group)
```

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)**
- Navigate to **Log Groups**
- Select specific log group from above output: for example **/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/gatewayID**
- Select `Search all log streams`
- In the CloudWatch logs, search for `observability-lambda-target___get_order_tool` keyword in the logs. <br/>
- Identify and note down the Trace ID corresponding to operation shown in the screenshot below.

For example: **68ed6dc01c0556e2735177ed3794422a**

![genai-cw-logs](images/12-cloudwatch-logs-mcptool-2.png)

- Navigate to **GenAI observability** -> **Bedrock AgentCore**
- Select **Gateways**

![genai-obs-gw](images/11-cloudwatch-gateways.png)

- Select **Traces** and search for the Trace ID: **68ed6dc01c0556e2735177ed3794422a**
- Click on **Trace ID** to view Spans and Latency. In this particular example, you can see that `InvokeTool` operation took 580ms and average span latency is 290ms.

![genai-obs-traces](images/13-cloudwatch-genai-traces-span.png)

**Note: You may need to adjust the Time Window on upper right hand corner accordingly if you dont see Traces.**

![timer-window](images/25-bedrock-timewindow.png)

- Scroll down further in the trace to check out `span metadata` for more details: <br/>

  `kind:SERVER` - tracks the overall execution details, tool invoked, gateway details, AWS request ID, trace and span ID.<br/>
  `kind:CLIENT` - covers the specific target that was invoked and details around it like target type, target execution time, target execution start and end times, etc.<br/>

  The screenshot below shows that the tool execution took 378ms (`execute_tool_latency_ms`) and the time the gateway took barring tool execution is 152 ms (`overhead_latency`) under `AgentCore.gateway.InvokeTool`.

![span-metadata](images/16-span-metadata.png)

### Using Amazon CloudWatch for detecting root cause of issues

**Scenario:** Sending invalid token to the gateway and checking the logs and spans.

#### Setting an invalid token

```python
token1 = "12345"
```

```python
print(cloudwatch_log_group)
```

#### Start Live Tail of CloudWatch Logs

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)**
- Navigate to **Log Groups**
- Select specific log group from above output: for example **/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/gatewayID**
- Click on **Start tailing**

![cloudwatch_tail](images/2-cloudwatch-live-tail.png)

#### Invoking specific gateway tool with invalid token

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

def create_streamable_http_transport():
    return streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token1}"})

client = MCPClient(create_streamable_http_transport)

## Define specific tool parameters
TOOL_NAME = "observability-lambda-target___get_order_tool"
ORDER_ID = "123"

with client:
    try:
        # Call the get_order_tool
        result = client.call_tool_sync(
            tool_use_id="get-order-id-123-call-1",
            name=TOOL_NAME,
            arguments={"orderId": ORDER_ID}
        )

        # Print the tool's response
        print(f"\nGet Order Tool Response for Order ID {ORDER_ID}:")
        print(f"Content: {result['content'][0]['text']}")

    except Exception as e:
        print(f"Error occurred while calling {TOOL_NAME}: {str(e)}")
```

## More information in Amazon CloudWatch Logs

The generated exception **does not provide useful information about the root cause of the issue**:

    raise MCPClientInitializationError("the client initialization failed") from e
    strands.types.exceptions.MCPClientInitializationError: the client initialization failed

To understand the root cause behind the exception, check the CloudWatch logs.

![rootcause](images/17-rootcause.png)

### More information in Traces and Spans

- Note down the value of `trace_id` from the above logs.
- In CloudWatch console, navigate to **GenAI observability** -> **Bedrock AgentCore**
- Select **Gateways**
- Select **Traces** and search for the Trace ID: **68ee6edf04de45005c702392328eb065**.
- You can find more details in the spans as shown in the screenshot below.

![troubleshooting-traces](images/18-troubleshooting-traces1.png)

### AgentCore gateway CloudWatch Metrics

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)**
- Select **Metrics** -> **All metrics**
- Select **AWS namespaces** -> **Bedrock-AgentCore**
- Select **Method, Name, Operation, Protocol, Resource**. For aggregration at Operation level, you can select **Method, Operation, Protocol, Resource**.
- Select Metrics of choice to create a dashboard or to view values.

![19-metrics](images/19-metrics.png)

### Understanding Agent Traces - Strands Agent on AgentCore runtime connecting to AgentCore gateway

This section deploys a Strands agent on AgentCore runtime that connects to the gateway. This is optional but demonstrates how agent-level traces appear in CloudWatch alongside gateway-level traces.

#### Prepare Strands agent code

Create a file named `strands_agent_gateway.py` with the following content. Replace the placeholder values with the outputs from your deployment:

```python
%%writefile strands_agent_gateway.py
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from datetime import datetime
from strands import Agent, tool
import logging
from strands.models import BedrockModel
from strands.tools import mcp
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
import os
import time
import functools
import asyncio
import json
import argparse

#### Substitute the values below with ones from your deployment

## GateWay URL
mcp_url = #<mcp_url> # Replace this. For example: "https://1demogatewayforlambda-tpwablbixre.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp"
## # Cognito parameters
user_pool_id =  #<user_pool_id> # Replace this. For example: "us-west-2_CpyXraYjW"
client_id = #<client_id> # Replace this. For example: "5ifm4heh1r3oa19r2mnngsvung"
client_secret =  #<client_secret> # Replace this. For example: "kchfnio0inegso43jrc2js0ntgqc5ku2uplhd9v7vp0bo8sp1g0"
scopeString =  #<scopeString> # Replace this. For example: "api/gateway"
token_endpoint =  #<token_endpoint> # Replace this. For example: "https://us-west-2_CpyXraYjW.auth.us-west-2.amazoncognito.com/oauth2/token"
region =  #<region> # Replace this. For example: "us-west-2"

###########################################

model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
bedrockmodel = BedrockModel(
    inference_profile_id= model_id,
    temperature=0,
    streaming=True,
)

## Defining  client as our GatewayClient
client = GatewayClient(region_name=region)
client.logger.setLevel(logging.DEBUG)

## Get token
client_config = {
    "user_pool_id": user_pool_id,
    "client_id": client_id,
    "client_secret": client_secret,
    "scope": scopeString,
    "region": region,
    "token_endpoint": token_endpoint
}

token_response = client.get_access_token_for_cognito(client_config)
access_token = token_response
print(access_token)


## Get gateway tools
def create_streamable_http_transport(mcp_url: str, access_token: str):
    return streamablehttp_client(mcp_url, headers={"Authorization": f"Bearer {access_token}"})

def get_full_tools_list(client):
    more_tools = True
    tools = []
    pagination_token = None
    while more_tools:
        tmp_tools = client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(tmp_tools)
        if tmp_tools.pagination_token is None:
            more_tools = False
        else:
            more_tools = True
            pagination_token = tmp_tools.pagination_token
    return tools

def run_agent(mcp_url: str, access_token: str, user_message: str):
    try:
        mcp_client = MCPClient(lambda: create_streamable_http_transport(mcp_url, access_token))

        with mcp_client:
            tools = get_full_tools_list(mcp_client)
            print(f"Found the following tools: {[tool.tool_name for tool in tools]}")
            agent = Agent(model=bedrockmodel,tools=tools, callback_handler=None)
            print("\nThinking...\n")
            print(user_message)
            result = agent(user_message)
        return result
    except Exception as e:
        print(f"Error in run_agent: {e}")
        return {"message": f"Error processing request: {str(e)}"}


## Define our app referencing the pre-built BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    try:
        """Process user input and return a response"""
        user_message = payload.get("prompt", "Hello")

        result = run_agent(mcp_url, access_token, user_message)
        print(result)
        return {"result": result.message}
    except Exception as e:
        return {"result": f"Error: {str(e)}"}

if __name__ == "__main__":
    app.run()
```

## Deploy the agent to AgentCore runtime

```python
from bedrock_agentcore_starter_toolkit import runtime
from boto3.session import Session
boto_session = Session()
region = boto_session.region_name

agentcore_runtime = runtime()
agent_name = "strands_demo_agent"
response = agentcore_runtime.configure(
    entrypoint="strands_agent_gateway.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=region,
    agent_name=agent_name
)
response
```

```python
launch_result = agentcore_runtime.launch()
```

```python
import time
time.sleep(20)
```

> [!NOTE]
> If you see the error "memory is still provisioning (current status: CREATING). Short-term memory takes 30-90 seconds to activate.", wait for a few more seconds before you retry.

```python
invoke_response = agentcore_runtime.invoke({"prompt": "list all tools"})
invoke_response
```

```python
invoke_response = agentcore_runtime.invoke({"prompt": "Check the order information for order id 123"})
invoke_response
```

### Check out the Traces in Amazon Cloudwatch - Agent Level

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)**
- Navigate to **GenAI observability** -> **Bedrock AgentCore**
- Select **Agents** -> **Traces**
- There are **two traces**, one for each prompt (list/tools and another one for invoking specific tool).

**Note: You may need to adjust the Time Window on upper right hand corner accordingly if you dont see Traces.**

![timer-window](images/25-bedrock-timewindow.png)

![Traces](images/14-traces-agentlevel-1.png)
![Traces](images/27-invoketools-agent-span.png)

#### Check out the Spans for a Trace

![Traces](images/15-spans-agent-1.png)

## observability with AWS CloudTrail

### Setting up CloudTrail for Management Events

The following steps create a CloudTrail trail that logs management events (CreateGateway, ListGateway, DeleteGateway, etc.) for AgentCore gateway.

#### Step 1: Create supporting resources

```python
import boto3
import json
import uuid
import time
from botocore.exceptions import ClientError

REGION = os.environ.get('AWS_DEFAULT_REGION', 'us-west-2')
account_id = boto3.client('sts').get_caller_identity()['Account']
trail_name = 'AgentCoreGatewayMgmtTrail'

def generate_s3_bucket_name(base_name):
    unique_id = uuid.uuid4().hex[:8]
    return f"{base_name}-{account_id}-{unique_id}".lower()

def create_bucket(bucket_name, region):
    try:
        s3_client = boto3.client('s3', region_name=region)
        if region == 'us-east-1':
            s3_client.create_bucket(Bucket=bucket_name)
        else:
            location = {'LocationConstraint': region}
            s3_client.create_bucket(Bucket=bucket_name, CreateBucketConfiguration=location)
        print(f"Bucket '{bucket_name}' created in region '{region}'")
        return True
    except ClientError as e:
        print(f"Error creating bucket: {e}")
        return False

def put_bucket_policy(bucket_name, account_id, region, trail_name):
    s3_client = boto3.client('s3')
    trail_arn = f"arn:aws:cloudtrail:{region}:{account_id}:trail/{trail_name}"
    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AWSCloudTrailAclCheck20150319",
                "Effect": "Allow",
                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                "Action": "s3:GetBucketAcl",
                "Resource": f"arn:aws:s3:::{bucket_name}",
                "Condition": {
                    "StringEquals": {"aws:SourceArn": trail_arn}
                }
            },
            {
                "Sid": "AWSCloudTrailWrite20150319",
                "Effect": "Allow",
                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{bucket_name}/AWSLogs/{account_id}/*",
                "Condition": {
                    "StringEquals": {
                        "s3:x-amz-acl": "bucket-owner-full-control",
                        "aws:SourceArn": trail_arn
                    }
                }
            }
        ]
    }
    policy_string = json.dumps(bucket_policy)
    s3_client.put_bucket_policy(Bucket=bucket_name, policy=policy_string)
    print(f"Bucket policy set for bucket '{bucket_name}' with trail '{trail_name}'.")
```

#### Step 2: Create IAM role and CloudWatch log group for CloudTrail

```python
log_group_name = f"/aws/cloudtrail/{trail_name}"
role_name = f"CloudTrail-{trail_name}-{REGION}"

def create_cloudwatch_role(role_name, account_id, region, log_group_name):
    """Create IAM role for CloudTrail to CloudWatch Logs"""
    iam = boto3.client('iam')

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "cloudtrail.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }

    role_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AWSCloudTrailCreateLogStream2014110",
                "Effect": "Allow",
                "Action": ["logs:CreateLogStream"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}:log-stream:{account_id}_CloudTrail_{region}*"
                ]
            },
            {
                "Sid": "AWSCloudTrailPutLogEvents20141101",
                "Effect": "Allow",
                "Action": ["logs:PutLogEvents"],
                "Resource": [
                    f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}:log-stream:{account_id}_CloudTrail_{region}*"
                ]
            }
        ]
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy)
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName=f"{role_name}-policy",
            PolicyDocument=json.dumps(role_policy)
        )
        time.sleep(5)
        return role['Role']['Arn']
    except iam.exceptions.EntityAlreadyExistsException:
        return iam.get_role[RoleName=role_name]('Role')['Arn']

cloudwatch_role_arn = create_cloudwatch_role(role_name, account_id, REGION, log_group_name)
print(f"CloudWatch role ARN: {cloudwatch_role_arn}")
```

```python
import time
time.sleep(20)
```

#### Step 3: Create S3 bucket and CloudWatch Log Group

```python
def create_cloudwatch_log_group(log_group_name, account_id, region):
    """Create CloudWatch Logs group and CloudTrail log stream"""
    logs_client = boto3.client('logs')
    log_stream_name = f"{account_id}_CloudTrail_{region}"

    try:
        logs_client.create_log_group(logGroupName=log_group_name)
        print(f"Created CloudWatch Logs group: {log_group_name}")

        logs_client.create_log_stream(
            logGroupName=log_group_name,
            logStreamName=log_stream_name
        )
        print(f"Created CloudWatch Logs stream: {log_stream_name}")

    except logs_client.exceptions.ResourceAlreadyExistsException as e:
        if "Log Group" in str(e):
            print(f"CloudWatch Logs group already exists: {log_group_name}")
            try:
                logs_client.create_log_stream(
                    logGroupName=log_group_name,
                    logStreamName=log_stream_name
                )
                print(f"Created CloudWatch Logs stream: {log_stream_name}")
            except logs_client.exceptions.ResourceAlreadyExistsException:
                print(f"CloudWatch Logs stream already exists: {log_stream_name}")
        else:
            print(f"CloudWatch Logs stream already exists: {log_stream_name}")

## Create S3 bucket for CloudTrail logs
s3_bucket_name = generate_s3_bucket_name("agentcore-gateway")
print(s3_bucket_name)
create_bucket(s3_bucket_name, REGION)
put_bucket_policy(s3_bucket_name, account_id, REGION, trail_name)

## Create CloudWatch Logs group
create_cloudwatch_log_group(log_group_name, account_id, REGION)
```

```python
import time
time.sleep(20)
```

## Step 4: Create CloudTrail for logging Management Events

```python
cloudtrail_client = boto3.client('cloudtrail', region_name=REGION)

## Create the trail
response = cloudtrail_client.create_trail(
    Name=trail_name,
    S3BucketName=s3_bucket_name,
    CloudWatchLogsLogGroupArn=f"arn:aws:logs:{REGION}:{account_id}:log-group:{log_group_name}:*",
    CloudWatchLogsRoleArn=cloudwatch_role_arn
)

## Define advanced event selector to only include management events for AgentCore gateway
advanced_event_selectors = [
    {
        'Name': 'AgentCoreGatewayManagementEvents',
        'FieldSelectors': [
            {
                'Field': 'eventCategory',
                'Equals': ['Management']
            }
        ]
    }
]

## Update the trail to use the advanced event selector
cloudtrail_client.put_event_selectors(
    TrailName=trail_name,
    AdvancedEventSelectors=advanced_event_selectors
)

## Start logging events
cloudtrail_client.start_logging(Name=trail_name)

print(f"CloudTrail trail '{trail_name}' created and logging management events for AgentCore gateway.")
```

## Verify on AWS Console

- Head to [AWS CloudTrail](https://console.aws.amazon.com/cloudtrailv2/) service in AWS Console
- Ensure that the AWS region is correct.
- Click on Trails and verify whether `AgentCoreGatewayMgmtTrail` is created successfully.

![ManagementTrail](images/28-cloudtrail.png)

```python
import time
time.sleep(20)
```

### Invoking tools/list and checking the API tracking via CloudTrail Events

```python
def list_gateways():
    """List all Bedrock Agent Core gateways"""
    try:
        gateway_client = boto3.client('bedrock-agentcore-control',
                                    region_name=REGION)

        print(REGION)

        response = gateway_client.list_gateways()
        print(response)

        if 'items' in response and response['items']:
            print("\nGateways found:")
            for gateway in response['items']:
                print(f"\nName: {gateway.get('name')}")
                print(f"gateway ID: {gateway.get('gatewayId')}")
                print(f"gateway URL: {gateway.get('gatewayUrl')}")
                print(f"Status: {gateway.get('status')}")
                print(f"Protocol Type: {gateway.get('protocolType')}")
                print(f"Authorizer Type: {gateway.get('authorizerType')}")
        else:
            print("No gateways found")

        return response.get('gateways', [])

    except Exception as e:
        print(f"Error listing gateways: {str(e)}")
        return []

## Call the function
gateways = list_gateways()
```

```python
import time
time.sleep(20)
```

> [!NOTE]
> It may take a few seconds for the log entries to get reflected in the CloudTrail logs.

## Check the CloudTrail Events

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)** <br/>
- Select CloudTrail Log Groups -> `/aws/cloudtrail/AgentCoreGatewayMgmtTrail` -> `Search all log streams` <br/>
- Search for `ListGateways` in the search box.

**ListGateways API call of the type `IAMUser`**<br/><br/>
![ListGateways](images/22-list-gateways-1.png)

Similary CreateGateway operations and other [gateway events](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-event-types.html) also logged under Management Events. <br/> <br/>
![ManagementEvents](images/21-cloudtrail-mgmt.png)

### Logging Data Events in CloudTrail

> [!WARNING]
> Data events will incur additional costs and are not enabled by default due to the volume.

```python
data_trail_name = 'AgentCoreGatewayDataTrail'
data_role_name = f"CloudTrail-{data_trail_name}-{REGION}"
data_log_group_name = f"/aws/cloudtrail/{data_trail_name}"

## Creating CloudWatch role for data trail
data_cloudwatch_role_arn = create_cloudwatch_role(data_role_name, account_id, REGION, data_log_group_name)
print(f"Data trail CloudWatch role ARN: {data_cloudwatch_role_arn}")
```

```python
import time
time.sleep(20)
```

```python
## Create S3 bucket for CloudTrail data event logs
data_s3_bucket_name = generate_s3_bucket_name("agentcore-gateway-data")
print(data_s3_bucket_name)
create_bucket(data_s3_bucket_name, REGION)
put_bucket_policy(data_s3_bucket_name, account_id, REGION, data_trail_name)

## Create CloudWatch Logs group
create_cloudwatch_log_group(data_log_group_name, account_id, REGION)
```

```python
import time
time.sleep(20)
```

```python
cloudtrail_client = boto3.client('cloudtrail', region_name=REGION)

## Create the trail
response = cloudtrail_client.create_trail(
    Name=data_trail_name,
    S3BucketName=data_s3_bucket_name,
    CloudWatchLogsLogGroupArn=f"arn:aws:logs:{REGION}:{account_id}:log-group:{data_log_group_name}:*",
    CloudWatchLogsRoleArn=data_cloudwatch_role_arn
)

## Define advanced event selector to only include data events for AgentCore gateway
advanced_event_selectors = [
    {
        'Name': 'AgentCoreGatewayDataEvents',
        'FieldSelectors': [
            {
                'Field': 'eventCategory',
                'Equals': ['Data']
            },
            {
                'Field': 'resources.type',
                'Equals': ['AWS::BedrockAgentCore::gateway']
            }
        ]
    }
]

## Update the trail to use the advanced event selector
cloudtrail_client.put_event_selectors(
    TrailName=data_trail_name,
    AdvancedEventSelectors=advanced_event_selectors
)

## Start logging events
cloudtrail_client.start_logging(Name=data_trail_name)

print(f"CloudTrail trail '{data_trail_name}' created and logging data events for AgentCore gateway.")
```

## Verify on AWS Console

- Head to [AWS CloudTrail](https://console.aws.amazon.com/cloudtrailv2/) service in AWS Console
- Ensure that the AWS region is correct.
- Click on Trails and verify whether `AgentCoreGatewayDataTrail` is created successfully.

![ManagementTrail](images/29-data-cloudtrail1.png)

```python
import time
time.sleep(20)
```

### Obtaining token for AgentCore gateway

```python
auth_string = f"{gateway_client_id}:{gateway_client_secret}"
auth_b64 = base64.b64encode(auth_string.encode('utf-8')).decode('utf-8')

token_response = requests.post(
    token_endpoint,
    headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'Authorization': f'Basic {auth_b64}'
    },
    data={
        'grant_type': 'client_credentials',
        'scope': 'api/gateway'
    }
)

token = token_response.json()["access_token"]
print("Token obtained successfully")
```

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

print(gateway_url)

def create_streamable_http_transport():
    return streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"})

client = MCPClient(create_streamable_http_transport)

## Define specific tool parameters
TOOL_NAME = "observability-lambda-target___get_order_tool"
ORDER_ID = "123"

with client:
    try:
        # Call the get_order_tool
        result = client.call_tool_sync(
            tool_use_id="get-order-id-123-call-1",
            name=TOOL_NAME,
            arguments={"orderId": ORDER_ID}
        )

        # Print the tool's response
        print(f"\nGet Order Tool Response for Order ID {ORDER_ID}:")
        print(f"Content: {result['content'][0]['text']}")

    except Exception as e:
        print(f"Error occurred while calling {TOOL_NAME}: {str(e)}")
```

## Check the CloudTrail Events

- Head to **[Amazon CloudWatch console](https://console.aws.amazon.com/cloudwatch/home)** <br/>
- Select CloudTrail Log Groups -> `/aws/cloudtrail/AgentCoreGatewayDataTrail` -> `Search all log streams` <br/>

> [!NOTE]
> It may take a few seconds for the log entries to get reflected in the CloudTrail logs.

![cloudtrail-data](images/23-cloudtrail-data.png)

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

### Step 1: Delete observability resources (CloudTrail, CloudWatch deliveries)

```bash
uv run python scripts/observability/cleanup_observability.py
```

This script deletes CloudTrail trails, S3 buckets, CloudWatch log deliveries, delivery sources, delivery destinations, log groups, and IAM roles created for observability. If some resources fail to delete, you may need to delete them manually from the AWS Console.

### Step 2: Delete AgentCore runtime agent (if deployed)

```bash
uv run python scripts/observability/cleanup_runtime.py
```

### Step 3: Delete gateway and targets

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
agentcore remove gateway-target --name observability-lambda-target -y
agentcore remove gateway --name observability-gateway -y
agentcore deploy --yes
```

### Step 4: Delete the Lambda function

```bash
aws lambda delete-function --function-name observability-gateway-lambda
```

### Step 5: Delete the Cognito stack (if no longer needed by other tutorials)

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AgentCore gateway observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-observability.html)
- [Amazon CloudWatch](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/)
- [AWS CloudTrail](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/)
- [gateway Event Types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-event-types.html)
