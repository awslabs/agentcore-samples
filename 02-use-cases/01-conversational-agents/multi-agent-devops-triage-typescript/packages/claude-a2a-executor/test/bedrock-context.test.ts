import { describe, expect, it } from 'vitest';

import {
  bedrockCallContextBuilder,
  extractBedrockContext,
  getBedrockContext,
  runWithBedrockContext,
} from '../src/index.js';

describe('extractBedrockContext', () => {
  it('extracts the AgentCore runtime headers into typed fields', () => {
    const context = extractBedrockContext({
      'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-1',
      'x-amzn-bedrock-agentcore-runtime-request-id': 'req-1',
      workloadaccesstoken: 'token-1',
      oauth2callbackurl: 'https://example.com/callback',
    });

    expect(context.sessionId).toBe('sess-1');
    expect(context.requestId).toBe('req-1');
    expect(context.workloadAccessToken).toBe('token-1');
    expect(context.oauth2CallbackUrl).toBe('https://example.com/callback');
  });

  it('generates a request id when the header is absent', () => {
    const context = extractBedrockContext({});

    expect(context.requestId).toMatch(/^[0-9a-f]{8}-[0-9a-f-]{27}$/);
    expect(context.sessionId).toBeUndefined();
    expect(context.workloadAccessToken).toBeUndefined();
    expect(context.oauth2CallbackUrl).toBeUndefined();
  });

  it('forwards only Authorization and runtime-custom headers', () => {
    const context = extractBedrockContext({
      authorization: 'Bearer abc',
      'x-amzn-bedrock-agentcore-runtime-custom-tenant': 'acme',
      'content-type': 'application/json',
      'x-forwarded-for': '10.0.0.1',
      'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-1',
    });

    expect(context.headers).toEqual({
      authorization: 'Bearer abc',
      'x-amzn-bedrock-agentcore-runtime-custom-tenant': 'acme',
    });
  });

  it('joins repeated header values', () => {
    const context = extractBedrockContext({
      'x-amzn-bedrock-agentcore-runtime-custom-tag': ['a', 'b'],
    });

    expect(context.headers['x-amzn-bedrock-agentcore-runtime-custom-tag']).toBe('a, b');
  });
});

describe('runWithBedrockContext / getBedrockContext', () => {
  it('propagates the context across await boundaries', async () => {
    const context = extractBedrockContext({
      'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-als',
    });

    const seen = await runWithBedrockContext(context, async () => {
      await new Promise((resolve) => setTimeout(resolve, 1));
      return getBedrockContext();
    });

    expect(seen).toBe(context);
  });

  it('returns undefined outside a request scope', () => {
    expect(getBedrockContext()).toBeUndefined();
  });
});

describe('bedrockCallContextBuilder', () => {
  it('stores the Bedrock fields in the ServerCallContext state', () => {
    const headers = {
      'x-amzn-bedrock-agentcore-runtime-session-id': 'sess-1',
      'x-amzn-bedrock-agentcore-runtime-request-id': 'req-1',
      workloadaccesstoken: 'token-1',
    };

    const callContext = bedrockCallContextBuilder({
      extensions: undefined,
      user: undefined,
      headers,
    });

    expect(callContext.state.get('sessionId')).toBe('sess-1');
    expect(callContext.state.get('requestId')).toBe('req-1');
    expect(callContext.state.get('workloadAccessToken')).toBe('token-1');
    expect(callContext.state.get('headers')).toBe(headers);
  });

  it('reuses the ambient context so request ids stay consistent', () => {
    const ambient = extractBedrockContext({});

    const callContext = runWithBedrockContext(ambient, () =>
      bedrockCallContextBuilder({ extensions: undefined, user: undefined, headers: {} }),
    );

    expect(callContext.state.get('requestId')).toBe(ambient.requestId);
  });

  it('passes the requested protocol version through', () => {
    const callContext = bedrockCallContextBuilder({
      extensions: undefined,
      user: undefined,
      headers: {},
      requestedVersion: '1.0',
    });

    expect(callContext.requestedVersion).toBe('1.0');
  });
});
