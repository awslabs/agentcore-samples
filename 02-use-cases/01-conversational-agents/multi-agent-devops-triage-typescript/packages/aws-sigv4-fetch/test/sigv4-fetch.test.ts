import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createSigV4Fetch } from '../src/sigv4-fetch.js';

// Deliberately fake, scanner-safe test credentials — signing only needs
// syntactically valid strings.
const CREDS = {
  accessKeyId: 'AKIDEXAMPLE',
  secretAccessKey: 'test-'.repeat(8),
};

describe('createSigV4Fetch', () => {
  let captured: Request | undefined;

  beforeEach(() => {
    captured = undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        captured = new Request(input, init);
        return new Response('{}', { status: 200 });
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('adds a SigV4 Authorization header scoped to service and region', async () => {
    const signedFetch = createSigV4Fetch({
      service: 'bedrock-agentcore',
      region: 'us-east-1',
      credentials: CREDS,
    });

    await signedFetch('https://gateway.example.com/mcp', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'tools/list' }),
    });

    expect(captured).toBeDefined();
    const auth = captured!.headers.get('authorization');
    expect(auth).toMatch(/^AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE\//);
    expect(auth).toContain('/us-east-1/bedrock-agentcore/aws4_request');
    expect(captured!.headers.get('x-amz-date')).toMatch(/^\d{8}T\d{6}Z$/);
  });

  it('signs the body (payload hash changes with content)', async () => {
    const signedFetch = createSigV4Fetch({
      service: 'bedrock-agentcore',
      region: 'us-east-1',
      credentials: CREDS,
    });

    await signedFetch('https://gateway.example.com/mcp', {
      method: 'POST',
      body: 'payload-one',
    });
    const authOne = captured!.headers.get('authorization');

    await signedFetch('https://gateway.example.com/mcp', {
      method: 'POST',
      body: 'payload-two',
    });
    const authTwo = captured!.headers.get('authorization');

    expect(authOne).not.toEqual(authTwo);
  });

  it('preserves original headers and method', async () => {
    const signedFetch = createSigV4Fetch({
      service: 'bedrock-agentcore',
      region: 'us-east-1',
      credentials: CREDS,
    });

    await signedFetch('https://gateway.example.com/mcp?foo=bar', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream' },
      body: '{}',
    });

    expect(captured!.method).toBe('POST');
    expect(captured!.headers.get('content-type')).toBe('application/json');
    expect(captured!.headers.get('accept')).toBe('application/json, text/event-stream');
    expect(new URL(captured!.url).searchParams.get('foo')).toBe('bar');
  });
});
