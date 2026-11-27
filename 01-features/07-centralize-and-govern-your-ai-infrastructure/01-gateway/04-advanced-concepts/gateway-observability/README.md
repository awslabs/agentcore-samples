# Diagnose an AgentCore Gateway request with CloudWatch and CloudTrail

Use this **read-only walkthrough** to investigate a request to an existing sandbox Gateway. You do not need to deploy a Strands agent, install a deployment CLI, create Cognito credentials or change a trail. Use the account and Region that own the Gateway. Ask the owner for its ARN, the UTC time window of a synthetic request, the operation (`tools/list` or `tools/call`), and its request or trace ID if available. Never collect bearer tokens, authorization headers or raw customer prompts to correlate events.

## 1. Decide which question you're answering

| Question | First place to look | What the evidence does **not** show |
|---|---|---|
| Did traffic, latency or errors change? | Gateway metrics in CloudWatch | An aggregate metric cannot identify a particular caller or payload. |
| Which step took time or returned an error? | Gateway logs and trace spans for the request, where delivery is configured | A Gateway span alone does not prove a target completed its work. |
| Who changed the Gateway or a target? | CloudTrail management events | A configuration change is not a tool invocation. |
| Was a particular Gateway data-plane action recorded? | The owner's CloudTrail trail or event data store configured for those data events | Management event history alone cannot answer this question. |

## 2. Follow a single request

1. In CloudWatch, select the Gateway's Region and inspect its metrics over the recorded time window. Compare the interval with an earlier one using the same period and dimensions. This helps distinguish a single slow request from a broader change.
2. In the log group selected by the Gateway owner, narrow to the time window and request ID. Distinguish tool discovery from tool execution: a successful `tools/list` says nothing about authorization, connectivity or latency for `tools/call`.
3. If a trace ID is available, open that trace and inspect the **individual span durations and statuses**. For a slow tool call, compare the Gateway operation with the target-side operation before attributing latency to either. If the caller is an instrumented Runtime, look for the caller span in the same trace. If propagation or sampling is missing, report that gap instead of joining unrelated requests by timestamp.

![Example Gateway metrics in CloudWatch](images/19-metrics.png)

![Example of spans associated with a Gateway trace](images/13-cloudwatch-genai-traces-span.png)

## 3. Correlate with CloudTrail

If a working call began failing, check for Gateway, target or authorization configuration changes near the first failure. Check the event time, resource identifier, actor and operation. Use a matching request identifier when available; don't assume that a CloudTrail event ID and a distributed trace ID are interchangeable. For data-plane auditing, have the trail owner verify that the event selector covered this resource and action **when the call occurred**. Turning on collection later cannot reconstruct a missing event.

![Example Gateway management events in CloudTrail](images/21-cloudtrail-mgmt.png)

![Example Gateway data events when collection is enabled](images/23-cloudtrail-data.png)

**No results?** Check account, Region, identifier, time zone, retention, sampling, delivery status and read permissions before concluding there was no call. Delivery and ingestion can lag. Report what you observed separately from what was not collected.

## Configuration and ownership

For the current setup procedure, see [Gateway observability data](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-gateway-metrics.html), [CloudWatch Logs deliveries](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_CreateDelivery.html), and [CloudTrail data events](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/logging-data-events-with-cloudtrail.html). Have the resource owner review destinations, retention, data access and costs before enabling additional collection. Do not change account-wide tracing settings, replace a shared trail's selectors, or delete a shared delivery to follow this guide.

This walkthrough creates nothing to clean up. The [historical setup](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/07-centralize-and-govern-your-ai-infrastructure/01-gateway/04-advanced-concepts/gateway-observability) also provisioned identity, a Lambda target, a Toolkit-hosted Runtime and observability infrastructure. If you deployed it, reconcile the original resource IDs and retention obligations with their owners. Do not delete trails, buckets, log groups, roles or targets based on naming patterns. The figures above illustrate the consoles; they are not evidence of a current deployment.
