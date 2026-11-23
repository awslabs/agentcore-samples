# Discover approved MCP servers and A2A agents

Use the `agent-registry` data plane to search an existing Registry from an application. This example returns the record ARN, version, descriptor type and metadata field names. Search results are **data, not trusted instructions or destinations for credentials**. Even an approved record does not grant permission to call a service. The script never opens a discovered URL or invokes a tool.

Use a consumer profile authorized for `agent-registry:SearchDiscoverableRegistryRecords` on the selected Registry. From this directory, install the same supported boto3 version as the [publisher workflow](../registry-synchronize-mcpserver/) in a fresh Python environment:

```bash
python -m pip install -r ../registry-synchronize-mcpserver/requirements.txt
python discover.py --registry-arn "$REGISTRY_ARN" --query 'order status'
```

A fresh [publisher draft](../publish-agentcore-tools-in-registry/) is not visible until separately approved. Search reads at most ten pages, checks the record identity against the requested Registry, skips duplicates and returns up to ten results by default. No records or infrastructures are changed; no cleanup is needed. If your application needs invocation, bind a selected record to an independently approved endpoint, protocol and authorization mechanism. Do not send a reused OAuth bearer to arbitrary metadata URLs.

The [historical orchestration demo](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/07-centralize-and-govern-your-ai-infrastructure/03-registry/03-advanced/discovery-and-invocation-at-runtime) provisioned a Gateway, identity resources and multiple model-backed runtimes, and dispatched calls automatically. This search-only demo does not reproduce model routing or live MCP/A2A calls. If you deployed the old version, reconcile the IDs and ownership from that deployment; do not run a blanket delete of IAM policies, secrets or repositories.
