import express from 'express';
import { z } from 'zod';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';

import { findRunbook, lookupService, SERVICES } from './catalog-data.js';

/**
 * Local stand-in for AgentCore Gateway + its Lambda service-catalog target.
 *
 * It exposes the same two tools over MCP streamable HTTP (no auth) that the
 * Gateway exposes in deployed mode (SigV4). The runbook worker's MCP client
 * config is identical in both modes except for the URL and signing.
 */

const PORT = Number(process.env.PORT ?? 8900);

function buildServer(): McpServer {
  const server = new McpServer({ name: 'service-catalog', version: '1.0.0' });

  server.registerTool(
    'lookup_service',
    {
      description:
        'Look up a service in the service catalog: owning team, escalation contact, tier, and dependencies. ' +
        `Known services: ${Object.keys(SERVICES).join(', ')}.`,
      inputSchema: { service: z.string().describe('Service name, e.g. "orders-api"') },
    },
    async ({ service }) => {
      const record = lookupService(service);
      if (!record) {
        return {
          content: [
            {
              type: 'text',
              text: `Service "${service}" not found. Known services: ${Object.keys(SERVICES).join(', ')}`,
            },
          ],
          isError: true,
        };
      }
      return { content: [{ type: 'text', text: JSON.stringify(record, null, 2) }] };
    },
  );

  server.registerTool(
    'get_runbook',
    {
      description:
        'Get the runbook steps for a service and symptom (e.g. "latency", "errors").',
      inputSchema: {
        service: z.string().describe('Service name, e.g. "orders-api"'),
        symptom: z.string().describe('Observed symptom, e.g. "latency spike"'),
      },
    },
    async ({ service, symptom }) => {
      const runbook = findRunbook(service, symptom);
      if (!runbook) {
        return {
          content: [{ type: 'text', text: `No runbook found for service "${service}".` }],
          isError: true,
        };
      }
      return { content: [{ type: 'text', text: JSON.stringify(runbook, null, 2) }] };
    },
  );

  return server;
}

const app = express();
app.use(express.json());

app.get('/ping', (_req, res) => {
  res.json({ status: 'Healthy' });
});

// Stateless mode: a fresh server+transport per request, no session tracking.
// This mirrors AgentCore Gateway, which is also stateless per MCP call.
app.post('/mcp', async (req, res) => {
  const server = buildServer();
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on('close', () => {
    void transport.close();
    void server.close();
  });
  await server.connect(transport);
  await transport.handleRequest(req, res, req.body);
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`[mock-service-catalog] MCP server on 0.0.0.0:${PORT}/mcp`);
});
