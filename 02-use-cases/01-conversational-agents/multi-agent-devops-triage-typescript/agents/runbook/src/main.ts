import type { McpServerConfig } from '@anthropic-ai/claude-agent-sdk';
import {
  buildAgentCard,
  ClaudeAgentExecutor,
  serveA2A,
} from '@sample/claude-a2a-executor';
import { createMcpProxy, createSigV4Fetch } from '@sample/aws-sigv4-fetch';

/**
 * Runbook worker — an A2A server on the AgentCore Runtime A2A protocol path
 * (port 9000) that consumes the service-catalog tool over MCP.
 *
 * Local mode: SERVICE_CATALOG_MCP_URL points at the local mock MCP server
 * (scripts/mock-service-catalog), plain streamable HTTP — the Claude Agent
 * SDK's built-in `type: 'http'` MCP client connects directly.
 *
 * Gateway mode: GATEWAY_MCP_URL points at an AgentCore Gateway, which
 * requires SigV4-signed MCP requests (IAM inbound auth). The SDK's MCP
 * client can't sign, so an in-process proxy (`createMcpProxy`) mirrors the
 * Gateway's tools over a SigV4-signing fetch and is handed to the SDK as an
 * in-process MCP server — no localhost signing sidecar.
 */

const SYSTEM_PROMPT = `You are a runbook and service-ownership specialist for DevOps incident triage.
When asked about a service, use the service-catalog MCP tools to look up the
service owner, escalation contact, and runbook steps. Always call the tools —
do not answer ownership or runbook questions from memory.
Reply with a concise summary: owning team, escalation contact, and the runbook
steps relevant to the reported symptom.`;

const PORT = Number(process.env.PORT ?? 9000);
const gatewayMcpUrl = process.env.GATEWAY_MCP_URL;
const localMcpUrl = process.env.SERVICE_CATALOG_MCP_URL ?? 'http://localhost:8900/mcp';

async function buildCatalogServer(): Promise<McpServerConfig> {
  if (gatewayMcpUrl) {
    const region = process.env.AWS_REGION ?? 'us-east-1';
    const proxy = await createMcpProxy({
      url: gatewayMcpUrl,
      name: 'service-catalog',
      fetchImpl: createSigV4Fetch({ service: 'bedrock-agentcore', region }),
    });
    return { type: 'sdk', name: 'service-catalog', instance: proxy };
  }
  return { type: 'http', url: localMcpUrl, alwaysLoad: true };
}

const executor = new ClaudeAgentExecutor({
  systemPrompt: SYSTEM_PROMPT,
  queryOptions: {
    env: { ...process.env, CLAUDE_CODE_USE_BEDROCK: '1' },
    model: process.env.ANTHROPIC_MODEL,
    // The only capability this worker needs is the catalog MCP server; all
    // built-in tools are disabled.
    tools: [],
    mcpServers: { 'service-catalog': await buildCatalogServer() },
    // Auto-approve every catalog tool (server-level spec): this worker is
    // non-interactive, and Gateway prefixes Lambda-target tool names with
    // "<target>___", so exact names differ between local and Gateway mode.
    allowedTools: ['mcp__service-catalog'],
    maxTurns: 8,
    settingSources: [],
  },
});

await serveA2A({
  agentCard: buildAgentCard({
    name: 'runbook',
    description:
      'Looks up service ownership, escalation contacts, and runbook steps from the service catalog.',
    skills: [
      {
        id: 'lookup-runbook',
        name: 'Look up runbook',
        description:
          'Fetches service ownership and runbook steps for a service via the service-catalog tool.',
        tags: ['runbook', 'service-catalog', 'incident-triage'],
        examples: ['Who owns the checkout API and what is the runbook for elevated latency?'],
      },
    ],
    // Local A2A clients route via the URL in the card, so it must carry the
    // actual listen port. When deployed, AGENTCORE_RUNTIME_URL wins.
    port: PORT,
  }),
  executor,
  port: PORT,
});
