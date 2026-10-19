import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

export interface McpProxyOptions {
  /** Remote MCP endpoint, e.g. the AgentCore Gateway URL (…/mcp). */
  url: string;
  /** Fetch used for upstream requests — pass a SigV4-signing fetch. */
  fetchImpl?: typeof fetch;
  /** Proxy server name shown to the model. Defaults to "gateway". */
  name?: string;
}

/**
 * Builds an in-process `McpServer` that mirrors a remote MCP endpoint's
 * tools and forwards `tools/list` / `tools/call` to it over a custom fetch.
 *
 * Why this exists: the Claude Agent SDK's own `type: 'http'` MCP client
 * cannot SigV4-sign requests, which AgentCore Gateway requires for IAM
 * inbound auth. Passing this proxy as an SDK MCP server
 * (`McpSdkServerConfigWithInstance`) keeps the signing in-process — no
 * localhost sidecar, no unauthenticated listener.
 *
 * Handlers are registered on the low-level `Server` so upstream JSON
 * Schemas pass through verbatim (no Zod round trip).
 */
export async function createMcpProxy(options: McpProxyOptions): Promise<McpServer> {
  const upstream = new Client({ name: `${options.name ?? 'gateway'}-proxy`, version: '1.0.0' });
  await upstream.connect(
    new StreamableHTTPClientTransport(new URL(options.url), {
      fetch: options.fetchImpl,
    }),
  );

  const proxy = new McpServer(
    { name: options.name ?? 'gateway', version: '1.0.0' },
    { capabilities: { tools: {} } },
  );

  proxy.server.setRequestHandler(ListToolsRequestSchema, async () => {
    const { tools } = await upstream.listTools();
    return { tools };
  });

  proxy.server.setRequestHandler(CallToolRequestSchema, async (request) => {
    return upstream.callTool({
      name: request.params.name,
      arguments: request.params.arguments,
    });
  });

  return proxy;
}
