import { existsSync } from 'node:fs';
import type { Server } from 'node:http';
import express from 'express';
import type { Express } from 'express';
import type { AgentCard } from '@a2a-js/sdk';
import type { AgentExecutor, ServerCallContextBuilder, TaskStore } from '@a2a-js/sdk/server';
import { DefaultRequestHandler, InMemoryTaskStore } from '@a2a-js/sdk/server';
import {
  agentCardHandler,
  jsonRpcHandler,
  UserBuilder,
} from '@a2a-js/sdk/server/express';

import { buildAgentCard, withJsonRpcUrl } from './agent-card.js';
import {
  bedrockCallContextBuilder,
  extractBedrockContext,
  runWithBedrockContext,
} from './bedrock-context.js';
import { logEvent } from './log.js';

/**
 * AgentCore Runtime health states. `HealthyBusy` tells the platform the
 * container is alive but should not receive new work.
 */
export type PingStatus = 'Healthy' | 'HealthyBusy';

export interface ServeA2AOptions {
  /**
   * Agent card to serve. When omitted, a generic fallback card is built
   * (mirroring the Python SDK's auto-built card). When provided AND
   * `AGENTCORE_RUNTIME_URL` is set, the card's JSONRPC interface URLs are
   * rewritten to the runtime URL so a deployed agent never advertises a
   * stale local address.
   */
  agentCard?: AgentCard;
  executor: AgentExecutor;
  /**
   * Defaults to the `PORT` env var, or 9000 (the AgentCore Runtime A2A
   * protocol port) when unset.
   */
  port?: number;
  /**
   * Defaults to 0.0.0.0 inside containers (required by the AgentCore
   * container contract; detected via /.dockerenv or DOCKER_CONTAINER) and
   * 127.0.0.1 otherwise — mirroring the Python SDK's host auto-detection.
   */
  host?: string;
  /**
   * Custom health reporter for `GET /ping`, e.g. returning `HealthyBusy`
   * while a long task runs so AgentCore stops routing new work here. On a
   * thrown error the server falls back to `Healthy` (matching the Python
   * SDK). Defaults to always-`Healthy`.
   */
  pingHandler?: () => PingStatus | Promise<PingStatus>;
  /** Task persistence; defaults to a per-server `InMemoryTaskStore`. */
  taskStore?: TaskStore;
  /**
   * `ServerCallContext` factory for the JSON-RPC handler; defaults to
   * {@link bedrockCallContextBuilder}, which mirrors the AgentCore headers
   * into `ServerCallContext.state`.
   */
  contextBuilder?: ServerCallContextBuilder;
}

function resolveAgentCard(provided: AgentCard | undefined, port?: number): AgentCard {
  const runtimeUrl = process.env.AGENTCORE_RUNTIME_URL;
  if (!provided) {
    // buildAgentCard resolves AGENTCORE_RUNTIME_URL itself; the skill
    // mirrors the Python SDK's auto-built "main" skill. The port keeps the
    // fallback card's localhost URL pointing at the actual listen port
    // (Python threads resolved_port into runtime_url the same way).
    return buildAgentCard({
      name: 'agent',
      description: 'A Bedrock AgentCore agent',
      skills: [{ id: 'main', name: 'agent', description: 'A Bedrock AgentCore agent' }],
      port,
    });
  }
  return runtimeUrl ? withJsonRpcUrl(provided, runtimeUrl) : provided;
}

/** Port resolution shared by serveA2A and buildA2AApp: option → PORT env → 9000. */
function resolvePort(port: number | undefined): number {
  return port ?? Number(process.env.PORT ?? 9000);
}

/**
 * Assembles the Express app implementing the AgentCore Runtime A2A
 * contract without binding a port — the analog of the Python SDK's
 * `build_a2a_app`:
 *
 *  - JSON-RPC 2.0 endpoint at `POST /`
 *  - Agent card at `GET /.well-known/agent-card.json`
 *  - Health check at `GET /ping` (AgentCore polls this and kills
 *    containers that don't respond)
 *
 * Use this directly for embedding in an existing server or for
 * socket-less testing; use {@link serveA2A} to also listen.
 *
 * AgentCore Runtime's A2A path is a transparent proxy: InvokeAgentRuntime
 * payloads pass through to `POST /` unmodified, so there is no envelope to
 * unwrap here or anywhere downstream.
 */
export function buildA2AApp(
  options: Pick<
    ServeA2AOptions,
    'agentCard' | 'executor' | 'port' | 'pingHandler' | 'taskStore' | 'contextBuilder'
  >,
): Express {
  const agentCard = resolveAgentCard(options.agentCard, resolvePort(options.port));

  const requestHandler = new DefaultRequestHandler(
    agentCard,
    options.taskStore ?? new InMemoryTaskStore(),
    options.executor,
  );

  const app = express();

  // AgentCore Runtime health contract: 200 + {"status": "Healthy"} (or
  // "HealthyBusy" to shed new work). A failing custom handler degrades to
  // Healthy rather than failing the probe — a broken reporter shouldn't
  // get the container killed. Do NOT report a fresh timestamp on every
  // ping — that would keep sessions alive until MaxLifetime (see the A2A
  // protocol contract docs).
  app.get('/ping', (_req, res) => {
    Promise.resolve()
      .then(() => options.pingHandler?.() ?? 'Healthy')
      .catch((error) => {
        logEvent('server', 'ping.handler_failed', {
          agent: agentCard.name,
          error: error instanceof Error ? error.message : String(error),
        });
        return 'Healthy' as const;
      })
      .then((status) => res.json({ status }));
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
        agent: agentCard.name,
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
      // Mirror the Bedrock fields into ServerCallContext.state (default) so
      // executors can read them from requestContext.context.state.
      contextBuilder: options.contextBuilder ?? bedrockCallContextBuilder,
      legacyCompat: { enabled: true },
    }),
  );

  return app;
}

/**
 * Starts an A2A server implementing the AgentCore Runtime contract — the
 * TypeScript analog of the Python SDK's `serve_a2a`. Builds the app via
 * {@link buildA2AApp} and listens on the resolved host/port (see
 * {@link ServeA2AOptions} for the defaults).
 */
export function serveA2A(options: ServeA2AOptions): Promise<Server> {
  const port = resolvePort(options.port);
  // Bind all interfaces only where the container contract needs it; on a
  // developer machine an A2A agent has no business listening externally.
  const inContainer = existsSync('/.dockerenv') || Boolean(process.env.DOCKER_CONTAINER);
  const host = options.host ?? (inContainer ? '0.0.0.0' : '127.0.0.1');
  const agentCard = resolveAgentCard(options.agentCard, port);

  const app = buildA2AApp({ ...options, agentCard, port });

  return new Promise((resolve) => {
    const server = app.listen(port, host, () => {
      console.log(`[a2a] ${agentCard.name} listening on ${host}:${port}`);
      resolve(server);
    });
  });
}
