# Register metadata for an existing MCP server or A2A agent

Publishing a descriptor does not require deploying another Runtime. Use the shared [Registry record workflow](../registry-synchronize-mcpserver/) with a local JSON document reviewed by the service owner: an MCP server descriptor or an A2A agent card. Do not include credentials, private tool arguments or customer data. Inline submission does not fetch the advertised endpoint. An incomplete descriptor may be rejected by Registry.

Use an existing sandbox Registry without automatic approval, the correct Region and a publisher profile authorized for that Registry. Install the linked workflow's requirements. From this directory, keep the receipt in your current working directory:

```bash
helper=../registry-synchronize-mcpserver/registry_synchronize_mcpserver.py
python "$helper" draft --registry-id "$REGISTRY_ID" --kind a2a --descriptor /path/to/reviewed-agent-card.json
python "$helper" status
```

For an MCP descriptor, use `--kind mcp` instead. Submit only one record per working directory. The helper waits for DRAFT and does not approve the record. For the separate approval process see [Registry end-to-end](../../01-registry-end-to-end/). To remove an unpromoted draft, run `python "$helper" cleanup` from the same directory. It refuses to delete a promoted record. No source Runtime, role or Registry is created or deleted.

The [historical publisher](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/07-centralize-and-govern-your-ai-infrastructure/03-registry/03-advanced/publish-agentcore-tools-in-registry) also deployed, invoked and tore down two order-management runtimes. This guide does not replace those hosted agents. Existing operators must reconcile original resource IDs and ownership before deleting anything.
