import type { Server } from 'node:http';
import express from 'express';
import type { AgentCard } from '@a2a-js/sdk';
import type { AgentExecutor } from '@a2a-js/sdk/server';
import { DefaultRequestHandler, InMemoryTaskStore } from '@a2a-js/sdk/server';
import {
  agentCardHandler,
  jsonRpcHandler,
  UserBuilder,
} from '@a2a-js/sdk/server/express';

import {
  bedrockCallContextBuilder,
  extractBedrockContext,
  runWithBedrockContext,
} from './bedrock-context.js';
import { logEvent } from './log.js';

export interface ServeA2AOptions {
  agentCard: AgentCard;
  executor: AgentExecutor;
  /** Defaults to 9000 — the AgentCore Runtime A2A protocol port. */
  port?: number;
  /** Defaults to 0.0.0.0 — required by the AgentCore container contract. */
  host?: string;
}

/**
 * Starts an A2A server implementing the AgentCore Runtime contract — the
 * TypeScript analog of the Python SDK's `serve_a2a`:
 *
 *  - JSON-RPC 2.0 endpoint at `POST /`
 *  - Agent card at `GET /.well-known/agent-card.json`
 *  - Health check at `GET /ping` (AgentCore polls this and kills
 *    containers that don't respond)
 *
 * AgentCore Runtime's A2A path is a transparent proxy: InvokeAgentRuntime
 * payloads pass through to `POST /` unmodified, so there is no envelope to
 * unwrap here or anywhere downstream.
 */
export function serveA2A(options: ServeA2AOptions): Promise<Server> {
  const port = options.port ?? 9000;
  const host = options.host ?? '0.0.0.0';

  const requestHandler = new DefaultRequestHandler(
    options.agentCard,
    new InMemoryTaskStore(),
    options.executor,
  );

  const app = express();

  // AgentCore Runtime health contract: 200 + {"status": "Healthy"}.
  // Do NOT report a fresh timestamp on every ping — that would keep
  // sessions alive until MaxLifetime (see the A2A protocol contract docs).
  app.get('/ping', (_req, res) => {
    res.json({ status: 'Healthy' });
  });

  app.use(
    '/.well-known/agent-card.json',
    agentCardHandler({
      agentCardProvider: requestHandler,
      // AgentCore's documented card shape and the Python SDK ecosystem
      // still speak A2A v0.3; the compat layer serves both versions.
      legacyCompat: { enabled: true },
    }),
  );

  // Incoming A2A request log: without this, a worker that handled a task
  // shows nothing but "listening" lines in its log group. The JSON-RPC
  // method is in the POST body, which Express hasn't parsed yet here — log
  // it after the body parser inside the handler chain.
  app.use('/', (req, _res, next) => {
    if (req.method === 'POST') {
      logEvent('server', 'rpc.received', {
        agent: options.agentCard.name,
        contentLength: req.headers['content-length'],
      });
    }
    next();
  });

  // Bedrock context propagation (parity with the Python SDK's
  // BedrockCallContextBuilder): extract the AgentCore-injected headers and
  // open an AsyncLocalStorage scope so the executor — and anything it calls,
  // like identity's withApiKey — can reach them via getBedrockContext().
  app.use('/', (req, _res, next) => {
    if (req.method !== 'POST') return next();
    runWithBedrockContext(extractBedrockContext(req.headers), next);
  });

  app.use(
    jsonRpcHandler({
      requestHandler,
      // Authentication (SigV4/OAuth) is terminated by AgentCore Runtime in
      // front of the container; inside, requests are trusted.
      userBuilder: UserBuilder.noAuthentication,
      // Also mirror the Bedrock fields into ServerCallContext.state so
      // executors can read them from requestContext.context.state.
      contextBuilder: bedrockCallContextBuilder,
      legacyCompat: { enabled: true },
    }),
  );

  return new Promise((resolve) => {
    const server = app.listen(port, host, () => {
      console.log(`[a2a] ${options.agentCard.name} listening on ${host}:${port}`);
      resolve(server);
    });
  });
}
