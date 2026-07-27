import type { Server } from 'node:http';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { TaskState } from '@a2a-js/sdk';
import type { ExecutionEventBus, RequestContext } from '@a2a-js/sdk/server';
import { AgentEvent } from '@a2a-js/sdk/server';

import type { Task } from '@a2a-js/sdk';
import type { ServerCallContext, TaskStore } from '@a2a-js/sdk/server';
import { InMemoryTaskStore } from '@a2a-js/sdk/server';

import type { BedrockA2AContext } from '../src/index.js';
import { buildA2AApp, buildAgentCard, getBedrockContext, serveA2A } from '../src/index.js';

/**
 * Trivial executor with no Claude Agent SDK dependency: records the ambient
 * Bedrock context it observes, then completes immediately.
 */
class RecordingExecutor {
  observed: BedrockA2AContext | undefined;

  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    this.observed = getBedrockContext();
    const { taskId, contextId } = requestContext;
    eventBus.publish(
      AgentEvent.task({
        id: taskId,
        contextId,
        status: { state: TaskState.TASK_STATE_SUBMITTED, message: undefined, timestamp: undefined },
        artifacts: [],
        history: [],
        metadata: undefined,
      }),
    );
    eventBus.publish(
      AgentEvent.statusUpdate({
        taskId,
        contextId,
        status: { state: TaskState.TASK_STATE_COMPLETED, message: undefined, timestamp: undefined },
        metadata: undefined,
      }),
    );
  }

  async cancelTask(): Promise<void> {}
}

function listenPort(server: Server): number {
  const address = server.address();
  if (address === null || typeof address === 'string') throw new Error('no port');
  return address.port;
}

function listenHost(server: Server): string {
  const address = server.address();
  if (address === null || typeof address === 'string') throw new Error('no host');
  return address.address;
}

async function fetchAgentCard(server: Server): Promise<{ supportedInterfaces: { url: string }[] }> {
  const response = await fetch(
    `http://127.0.0.1:${listenPort(server)}/.well-known/agent-card.json`,
    { headers: { 'A2A-Version': '1.0' } },
  );
  return (await response.json()) as { supportedInterfaces: { url: string }[] };
}

describe('serveA2A defaults and card handling', () => {
  const servers: Server[] = [];

  async function serve(options: Partial<Parameters<typeof serveA2A>[0]> = {}): Promise<Server> {
    const server = await serveA2A({
      executor: new RecordingExecutor(),
      port: 0,
      ...options,
    });
    servers.push(server);
    return server;
  }

  afterEach(() => {
    while (servers.length > 0) servers.pop()?.close();
    vi.unstubAllEnvs();
  });

  it('rewrites a provided card interface URL when AGENTCORE_RUNTIME_URL is set', async () => {
    const runtimeUrl =
      'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn/invocations/';
    vi.stubEnv('AGENTCORE_RUNTIME_URL', runtimeUrl);

    const server = await serve({
      agentCard: buildAgentCard({ name: 'Provided', description: 'x', url: 'http://stale:9000/' }),
    });

    const card = await fetchAgentCard(server);
    expect(card.supportedInterfaces.map((i) => i.url)).toContain(runtimeUrl);
    expect(card.supportedInterfaces.map((i) => i.url)).not.toContain('http://stale:9000/');
  });

  it('auto-builds a fallback agent card when none is provided', async () => {
    const server = await serve();

    const response = await fetch(
      `http://127.0.0.1:${listenPort(server)}/.well-known/agent-card.json`,
      { headers: { 'A2A-Version': '1.0' } },
    );
    expect(response.status).toBe(200);
    const card = (await response.json()) as { name: string; skills: unknown[] };
    expect(card.name).toBeTruthy();
    expect(card.skills.length).toBeGreaterThanOrEqual(1);
  });

  it('advertises the actual listen port on the auto-built card', async () => {
    // Fixed non-default port: with port 0 the ephemeral port isn't known
    // before listen, so assert against an explicit one like Python does
    // (serve_a2a threads resolved_port into the card's runtime_url).
    const server = await serve({ port: 3005 });

    const card = await fetchAgentCard(server);
    expect(card.supportedInterfaces.map((i) => i.url)).toContain('http://localhost:3005/');
    expect(card.supportedInterfaces.map((i) => i.url)).not.toContain('http://localhost:9000/');
  });

  it('uses the PORT env var when no port option is given', async () => {
    vi.stubEnv('PORT', '0');
    const server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Port', description: 'x' }),
      executor: new RecordingExecutor(),
    });
    servers.push(server);

    // PORT=0 binds an ephemeral port — anything listening proves the env
    // var was consulted instead of the 9000 default.
    expect(listenPort(server)).toBeGreaterThan(0);
    expect(listenPort(server)).not.toBe(9000);
  });

  it('binds to loopback outside containers and 0.0.0.0 inside', async () => {
    const local = await serve({ agentCard: buildAgentCard({ name: 'L', description: 'x' }) });
    expect(listenHost(local)).toBe('127.0.0.1');

    vi.stubEnv('DOCKER_CONTAINER', '1');
    const container = await serve({ agentCard: buildAgentCard({ name: 'C', description: 'x' }) });
    expect(listenHost(container)).toBe('0.0.0.0');
  });
});

describe('serveA2A ping handler', () => {
  const servers: Server[] = [];

  afterEach(() => {
    while (servers.length > 0) servers.pop()?.close();
  });

  async function pingStatus(server: Server): Promise<{ status: string }> {
    const response = await fetch(`http://127.0.0.1:${listenPort(server)}/ping`);
    return (await response.json()) as { status: string };
  }

  it('reports a custom ping status such as HealthyBusy', async () => {
    const server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Busy', description: 'x' }),
      executor: new RecordingExecutor(),
      port: 0,
      pingHandler: () => 'HealthyBusy',
    });
    servers.push(server);

    expect(await pingStatus(server)).toEqual({ status: 'HealthyBusy' });
  });

  it('supports async ping handlers', async () => {
    const server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Async', description: 'x' }),
      executor: new RecordingExecutor(),
      port: 0,
      pingHandler: async () => 'Healthy',
    });
    servers.push(server);

    expect(await pingStatus(server)).toEqual({ status: 'Healthy' });
  });

  it('falls back to Healthy when the ping handler throws', async () => {
    const server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Broken', description: 'x' }),
      executor: new RecordingExecutor(),
      port: 0,
      pingHandler: () => {
        throw new Error('handler exploded');
      },
    });
    servers.push(server);

    const response = await fetch(`http://127.0.0.1:${listenPort(server)}/ping`);
    expect(response.status).toBe(200);
    expect((await response.json()) as { status: string }).toEqual({ status: 'Healthy' });
  });
});

describe('buildA2AApp', () => {
  it('returns an Express app without binding a port', async () => {
    const app = buildA2AApp({
      agentCard: buildAgentCard({ name: 'Embedded', description: 'x' }),
      executor: new RecordingExecutor(),
    });

    // Exercise it on an ephemeral socket we control ourselves.
    const server = app.listen(0, '127.0.0.1');
    await new Promise((resolve) => server.once('listening', resolve));
    try {
      const port = (server.address() as { port: number }).port;
      const response = await fetch(`http://127.0.0.1:${port}/ping`);
      expect((await response.json()) as { status: string }).toEqual({ status: 'Healthy' });
    } finally {
      server.close();
    }
  });
});

describe('serveA2A custom task store', () => {
  it('persists tasks through an injected TaskStore', async () => {
    const saved: Task[] = [];
    const inner = new InMemoryTaskStore();
    const recordingStore: TaskStore = {
      async save(task: Task, context: ServerCallContext): Promise<void> {
        saved.push(task);
        return inner.save(task, context);
      },
      load: inner.load.bind(inner),
      list: inner.list.bind(inner),
    };

    const server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Store', description: 'x' }),
      executor: new RecordingExecutor(),
      port: 0,
      taskStore: recordingStore,
    });
    try {
      const response = await fetch(`http://127.0.0.1:${listenPort(server)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          method: 'message/send',
          params: {
            message: {
              kind: 'message',
              messageId: 'msg-store',
              role: 'user',
              parts: [{ kind: 'text', text: 'hello' }],
            },
          },
        }),
      });
      expect(response.status).toBe(200);
      expect(saved.length).toBeGreaterThanOrEqual(1);
    } finally {
      server.close();
    }
  });
});

describe('serveA2A Bedrock context propagation', () => {
  const executor = new RecordingExecutor();
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    server = await serveA2A({
      agentCard: buildAgentCard({ name: 'Context Test', description: 'x' }),
      executor,
      port: 0,
      host: '127.0.0.1',
    });
    const address = server.address();
    if (address === null || typeof address === 'string') throw new Error('no port');
    baseUrl = `http://127.0.0.1:${address.port}`;
  });

  afterAll(() => {
    server.close();
  });

  it('exposes the AgentCore runtime headers to the executor via getBedrockContext', async () => {
    const response = await fetch(baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-e2e',
        'x-amzn-bedrock-agentcore-runtime-request-id': 'req-e2e',
        WorkloadAccessToken: 'token-e2e',
        'x-amzn-bedrock-agentcore-runtime-custom-tenant': 'acme',
      },
      body: JSON.stringify({
        jsonrpc: '2.0',
        id: 1,
        method: 'message/send',
        params: {
          message: {
            kind: 'message',
            messageId: 'msg-e2e',
            role: 'user',
            parts: [{ kind: 'text', text: 'hello' }],
          },
        },
      }),
    });

    expect(response.status).toBe(200);
    expect(executor.observed).toBeDefined();
    expect(executor.observed?.sessionId).toBe('sess-e2e');
    expect(executor.observed?.requestId).toBe('req-e2e');
    expect(executor.observed?.workloadAccessToken).toBe('token-e2e');
    expect(executor.observed?.headers['x-amzn-bedrock-agentcore-runtime-custom-tenant']).toBe(
      'acme',
    );
  });
});
