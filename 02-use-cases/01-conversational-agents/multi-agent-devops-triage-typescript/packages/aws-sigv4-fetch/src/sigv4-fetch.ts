import { Sha256 } from '@aws-crypto/sha256-js';
import { fromNodeProviderChain } from '@aws-sdk/credential-providers';
import { HttpRequest } from '@smithy/protocol-http';
import { SignatureV4 } from '@smithy/signature-v4';
import type { AwsCredentialIdentity, Provider } from '@smithy/types';

export interface SigV4FetchOptions {
  /**
   * SigV4 signing service name. AgentCore Runtime and Gateway both sign as
   * `bedrock-agentcore`.
   */
  service: string;
  region: string;
  credentials?: AwsCredentialIdentity | Provider<AwsCredentialIdentity>;
}

/**
 * Wraps global fetch so every request is SigV4-signed before it leaves the
 * process. This is the in-process alternative to the localhost
 * signing-proxy sidecar that teams hand-build today.
 *
 * Tradeoff vs a sidecar: in-process signing needs the HTTP client to accept
 * a custom fetch (both `@modelcontextprotocol/sdk` and `@a2a-js/sdk` do),
 * but removes a network hop, a second process to supervise, and an
 * unauthenticated localhost listener from the container.
 */
export function createSigV4Fetch(options: SigV4FetchOptions): typeof fetch {
  const signer = new SignatureV4({
    service: options.service,
    region: options.region,
    credentials: options.credentials ?? fromNodeProviderChain(),
    sha256: Sha256,
  });

  return async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(input, init);
    const url = new URL(request.url);

    const bodyText = request.body !== null ? await request.clone().text() : undefined;

    const headers: Record<string, string> = {};
    request.headers.forEach((value, key) => {
      // The signer computes its own Host header; a stale one breaks the signature.
      if (key.toLowerCase() !== 'host') headers[key] = value;
    });

    const signed = await signer.sign(
      new HttpRequest({
        method: request.method,
        protocol: url.protocol,
        hostname: url.hostname,
        port: url.port ? Number(url.port) : undefined,
        path: url.pathname,
        query: Object.fromEntries(url.searchParams.entries()),
        headers: { ...headers, host: url.host },
        body: bodyText,
      }),
    );

    return fetch(url, {
      method: signed.method,
      headers: signed.headers,
      body: bodyText,
      // Streaming SSE responses must not be buffered.
      signal: init?.signal ?? request.signal,
    });
  };
}
