import { randomUUID } from 'node:crypto';
import {
  ClientFactory,
  ClientFactoryOptions,
  DefaultAgentCardResolver,
  JsonRpcTransportFactory,
} from '@a2a-js/sdk/client';
import { createSigV4Fetch } from '@sample/aws-sigv4-fetch';

/**
 * A2A transport selection for the lead agent.
 *
 * Local mode (worker URL is plain http://host:port): default fetch.
 *
 * Deployed mode (worker URL is an AgentCore Runtime invocation URL,
 * https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<escaped-arn>/invocations/):
 * the same A2A JSON-RPC payloads go through the InvokeAgentRuntime HTTP
 * endpoint — AgentCore proxies them to the worker container unmodified.
 * Requests must be SigV4-signed (service: bedrock-agentcore) and carry the
 * runtime session header, so we hand the a2a-js client a signing fetch.
 */

export function runtimeUrlFromArn(runtimeArn: string, region: string): string {
  return `https://bedrock-agentcore.${region}.amazonaws.com/runtimes/${encodeURIComponent(
    runtimeArn,
  )}/invocations/`;
}

function isAgentCoreRuntimeUrl(url: string): boolean {
  return /^https:\/\/bedrock-agentcore(?:-[a-z0-9]+)?\.[a-z0-9-]+\.amazonaws\.com\//.test(url);
}

/**
 * Builds a ClientFactory for the given worker base URL. For AgentCore
 * Runtime URLs, every request (agent-card fetch included) is SigV4-signed
 * and pinned to one runtime session for the lifetime of this factory.
 */
export function createA2AClientFactory(baseUrl: string, region: string): ClientFactory {
  if (!isAgentCoreRuntimeUrl(baseUrl)) {
    return new ClientFactory();
  }

  const signedFetch = createSigV4Fetch({ service: 'bedrock-agentcore', region });
  // One A2A client conversation ↔ one AgentCore runtime session.
  const sessionId = randomUUID();

  const fetchWithSession: typeof fetch = (input, init) => {
    const request = input instanceof Request ? new Request(input, init) : new Request(input, init);
    const headers = new Headers(request.headers);
    headers.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sessionId);
    return signedFetch(new Request(request, { headers }));
  };

  return new ClientFactory(
    ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
      // Both the JSON-RPC transport and the card resolver must use the
      // signing fetch — the runtime's card endpoint requires SigV4 too.
      transports: [new JsonRpcTransportFactory({ fetchImpl: fetchWithSession })],
      cardResolver: new DefaultAgentCardResolver({
        fetchImpl: fetchWithSession,
        legacyCompat: { enabled: true },
      }),
    }),
  );
}
