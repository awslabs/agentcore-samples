import type { Server } from 'node:http';
import express from 'express';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { z } from 'zod';

import { createMcpProxy } from '../src/mcp-proxy.js';

/**
 * Stands in for an AgentCore Gateway: a streamable-HTTP MCP endpoint that
 * rejects requests lacking a SigV4 Authorization header — verifying the
 * proxy actually routes upstream traffic through the signing fetch.
 */
let server: Server;
let url: string;
const seenAuthHeaders: Array<string | undefined> = [];

beforeAll(async () => {
  const app = express();
  app.use(express.json());
  app.post('/mcp', async (req, res) => {
    seenAuthHeaders.push(req.headers.authorization);
    if (!req.headers.authorization?.startsWith('AWS4-HMAC-SHA256')) {
      res.status(403).json({ message: 'Missing SigV4 signature' });
      return;
    }
    const upstream = new McpServer({ name: 'fake-gateway', version: '1.0.0' });
    upstream.registerTool(
      'catalog___lookup_service',
      {
        description: 'Look up a service',
        inputSchema: { service: z.string() },
      },
      async ({ service }) => ({
        content: [{ type: 'text', text: `owner-of-${service}` }],
      }),
    );
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on('close', () => {
      void transport.close();
      void upstream.close();
    });
    await upstream.connect(transport);
    await transport.handleRequest(req, res, req.body);
  });

  await new Promise<void>((resolve) => {
    server = app.listen(0, '127.0.0.1', () => resolve());
  });
  const address = server.address();
  if (address === null || typeof address === 'string') throw new Error('no port');
  url = `http://127.0.0.1:${address.port}/mcp`;
});

afterAll(() => {
  server?.close();
});

/** Fake signing fetch: stamps a SigV4-shaped Authorization header. */
const fakeSigningFetch: typeof fetch = async (input, init) => {
  const request = input instanceof Request ? new Request(input, init) : new Request(input, init);
  const headers = new Headers(request.headers);
  headers.set(
    'authorization',
    'AWS4-HMAC-SHA256 Credential=TEST/20260723/us-east-1/bedrock-agentcore/aws4_request, SignedHeaders=host, Signature=fake',
  );
  return fetch(new Request(request, { headers }));
};

describe('createMcpProxy', () => {
  it('mirrors upstream tools and forwards calls through the signing fetch', async () => {
    const proxy = await createMcpProxy({
      url,
      name: 'service-catalog',
      fetchImpl: fakeSigningFetch,
    });

    // Talk to the proxy the same way the Claude Agent SDK does: as an MCP
    // client over an in-memory transport.
    const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
    const client = new Client({ name: 'test-client', version: '1.0.0' });
    await Promise.all([proxy.connect(serverTransport), client.connect(clientTransport)]);

    const { tools } = await client.listTools();
    expect(tools.map((t) => t.name)).toContain('catalog___lookup_service');

    const result = await client.callTool({
      name: 'catalog___lookup_service',
      arguments: { service: 'orders-api' },
    });
    const content = result.content as Array<{ type: string; text?: string }>;
    expect(content[0]?.text).toBe('owner-of-orders-api');

    // Every upstream request carried the (fake) SigV4 signature.
    expect(seenAuthHeaders.length).toBeGreaterThan(0);
    for (const header of seenAuthHeaders) {
      expect(header).toMatch(/^AWS4-HMAC-SHA256 /);
    }
  });

  it('fails fast when the upstream rejects unsigned requests', async () => {
    await expect(createMcpProxy({ url, name: 'unsigned' })).rejects.toThrow();
  });
});
