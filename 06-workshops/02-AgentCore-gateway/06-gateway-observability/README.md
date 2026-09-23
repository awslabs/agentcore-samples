AgentCore Gateway Observability Tutorial
# Configure Observability for AgentCore Gateway with Amazon CloudWatch and AWS CloudTrail

## Overview

Observability is a fundamental capability for the AgentCore Gateway because it provides comprehensive real-time insights into the functioning and performance of AI agents deployed through the gateway. By capturing and displaying key metrics such as request volumes, success rates, error patterns, latency for tool invocations, and authentication events, the observability features allow developers and operators to monitor the health and efficiency of their agent workflows continuously. This level of monitoring helps quickly identify anomalies or bottlenecks that could affect user experience or system reliability, enabling proactive troubleshooting and performance tuning.

Beyond high-level metrics, AgentCore Gateway observability offers detailed tracing of each agent’s workflow. Every action—from invoking tools to model calls and memory retrieval—is logged as spans and traces compliant with OpenTelemetry standards. This rich telemetry data provides developers with a transparent view into the internal decision-making processes of agents, including how each step was executed and its duration. Such granular traceability is invaluable for debugging complex failures or unexpected behaviors, as it allows engineers to drill down into the exact point of error or inefficiency. Additionally, by integrating with widely used monitoring platforms like Amazon CloudWatch, these observability features enable a unified and accessible operational overview.

Furthermore, observability supports compliance and governance requirements by offering audit trails of agent activity, which is critical for enterprise environments. It also facilitates optimization by revealing usage patterns and helping adjust agent workflows to reduce costs or improve speed. Ultimately, these observability capabilities transform the AgentCore Gateway from a black-box interface into a transparent, manageable system that supports reliable, scalable, and performant AI agent deployment in production environments.
 
## Observability with Amazon CloudWatch and AWS CloudTrail

* Amazon CloudWatch focuses on real-time performance monitoring and operational troubleshooting for AgentCore Gateway, providing detailed metrics and logs for latency, error rates, and usage patterns. 
* AWS CloudTrail focuses on security, compliance, and auditing by recording a full history of API calls and user actions related to the gateway. 

Together, they offer a holistic observability and governance framework for managing AgentCore Gateway in production.

![images/1-agentcore-gw-architecture.png]

#### AgentCore Gateway CloudWatch Metrics

Gateway publishes the following metrics to Amazon CloudWatch. They provide information about about API invocations, performance, and errors.

* **Invocations:** The total number of requests made to each Data Plane API. Each API call counts as one invocation regardless of the response status.

* **Throttles:** The number of requests throttled (status code 429) by the service.

* **SystemErrors:** The number of requests which failed with 5xx status code.

* **UserErrors:** The number of requests which failed with 4xx status code except 429.

* **Latency:** The time elapsed between when the service receives the request and when it begins sending the first response token. In other words, initial response time.

* **Duration:** The total time elapsed between receiving the request and sending the final response token. Represents complete end-to-end processing time of the request.

* **TargetExecutionTime:**  The total time taken to execute the target over Lambda / OpenAPI / etc. This helps determine the contribution of the target to the total Latency.

* **TargetType:** The total number of requests served by each type of target (MCP, Lambda, OpenAPI). 

#### AgentCore Gateway Cloudwatch Vended Logs

AgentCore logs the following information for gateway resources:

* Start and completion of gateway requests processing
* Error messages for Target configurations
* MCP Requests with missing or incorrect authorization headers
* MCP Requests with incorrect request parameters (tools, method)

AgentCore can output logs to Amazon CloudWatch, Amazon S3, or Firehose stream. This tutorial focuses on CloudWatch.

If you add Amazon CloudWatch Logs under AgentCore Gateway Log Delivery in the AWS console, these logs are stored under the default log group **/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/{gateway_id}**. You can also configure your custom log group starting with /**aws/vendedlogs/**. 

#### AgentCore Gateway CloudWatch Tracing 

Enabling tracing on the Amazon Bedrock AgentCore gateway provides deep insights into the behavior and performance of your AI agents and the tools they interact with. It captures the full execution path of a request as it moves through the gateway, which is essential for effective debugging, optimization, and auditing of complex agentic workflow. 

* **Traces - Top Level Container**

  * Represents the complete interaction context
  * Captures the full execution path starting from an agent invocation
  * May include multiple agent calls throughout the interaction
  * Provides the broadest view of the entire workflow

* **Requests - Individual Agent Invocations**

  * Represents a single request-response cycle within a trace
  * Each agent invocation creates a new request
  * Captures one complete call to an agent and its response
  * Multiple requests can exist within a single trace

* **Spans - Discrete Units of Work**

  * Represents specific, measurable operations within a request
  * Captures fine-grained steps like:
    * Component initialization
    * Tool executions
    * API calls
    * Processing steps
  * Has precise start/end timestamps for duration analysis

The relationship between these three observability components can be visualized as:

  Traces (highest level) - Represent complete user conversations or interaction contexts

  Requests (middle level) - Represent individual request-response cycles within a Trace

  Spans (lowest level) - Represent specific operations or steps within Request

          Trace 1
          ├── Request 1.1
          │   ├── Span 1.1.1
          │   ├── Span 1.1.2
          │   └── Span 1.1.3
          ├── Request 1.2
          │   ├── Span 1.2.1
          │   ├── Span 1.2.2
          │   └── Span 1.2.3
          └── Request 1.N

          Trace 2
          ├── Request 2.1
          │   ├── Span 2.1.1
          │   ├── Span 2.1.2
          │   └── Span 2.1.3
          ├── Request 2.2
          │   ├── Span 2.2.1
          │   ├── Span 2.2.2
          │   └── Span 2.2.3
          └── Request 2.N



#### AgentCore Gateway CloudTrail

AgentCore Gateway is fully integrated with AWS CloudTrail, which provides comprehensive logging and monitoring capabilities for **tracking API activity** and operational events within your gateway infrastructure.

CloudTrail captures two distinct types of events for AgentCore Gateway 
* Management events are logged automatically and capture control plane operations such as creating, updating, or deleting gateway resources 
* Data events, which provide information about resource operations performed on or within a gateway (also known as data plane operations), are high-volume activities that must be explicitly enabled as they are not logged by default 

CloudTrail captures all API calls for Gateway as events, including calls from the Gateway console and code calls to the Gateway APIs. Using the information collected by CloudTrail, you can determine the request that was made to Gateway, who made the request, when it was made, and additional details [3]. Management events provide information about management operations performed on resources in your AWS account, also known as control plane operations.

## Tutorials Overview

In these tutorials we will cover observability of AgentCore Gateway. 


| Information          | Details                                                   |
|:---------------------|:----------------------------------------------------------|
| Tutorial type        | Interactive                                               |
| AgentCore components | AgentCore Gateway, Amazon CloudWatch, AWS CloudTrail      |
| Agentic Framework    | Strands Agents                                            |
| Gateway Target type  | AWS Lambda                                                |
| Inbound Auth IdP     | Amazon Cognito                                            |
| Outbound Auth        | AWS IAM                                                   |
| LLM model            | Anthropic Claude Sonnet 4.0                               |
| Tutorial components  | AgentCore Gateway Observability with CloudWatch,CloudTrail|
| Tutorial vertical    | Cross-vertical                                            |
| Example complexity   | Easy                                                      |
| SDK used             | boto3                                                     |

#### Tutorial Details

* In this tutorial, we will create Bedrock AgentCore Gateway and add lambda as the target type with two tools: get_order and update_order. 
* We will create the log delivery group with destination as CloudWatch and observe the vended logs.
* We will enable Amazon CloudWatch Tracing and connect the trace ID found in vended logs with the Traces / Spans to dive deeper
* We will create AgentCore Runtime with Strands Agent and walk through the Spans.
* We will configure CloudTrail Management and Data Events and check some examples

### Resources

* [AgentCore generated gateway observability data](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-gateway-metrics.html)
* [Enable log destinations and tracing for AgentCore gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html#observability-configure-cloudwatch)
* [Logging AgentCore Gateway API calls with CloudTrail](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-cloudtrail.html)
* [Setting up AgentCore CloudWatch Metrics and Alarms](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-advanced-observability-metrics.html)
* [Logging Gateway API calls with CloudTrail](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-cloudtrail.html)
* [Observability Concepts](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-telemetry.html)

