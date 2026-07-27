import type { Server } from 'node:http';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { TaskState } from '@a2a-js/sdk';
import type { ExecutionEventBus, RequestContext } from '@a2a-js/sdk/server';
import { AgentEvent } from '@a2a-js/sdk/server';

import type { BedrockA2AContext } from '../src/index.js';
import { buildAgentCard, getBedrockContext, serveA2A } from '../src/index.js';

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
