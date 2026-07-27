import { describe, expect, it } from 'vitest';

import { runtimeUrlFromArn } from '../src/index.js';

const ARN = 'arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-agent-abc123';

describe('runtimeUrlFromArn', () => {
  it('builds the invocation URL with the ARN percent-encoded', () => {
    expect(runtimeUrlFromArn(ARN, 'us-east-1')).toBe(
      'https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/' +
        'arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2Fmy-agent-abc123' +
        '/invocations/',
    );
  });

  it('extracts the region from the ARN when not passed', () => {
    expect(runtimeUrlFromArn(ARN)).toContain('https://bedrock-agentcore.us-east-1.amazonaws.com/');
  });

  it('lets an explicit region override the ARN region', () => {
    expect(runtimeUrlFromArn(ARN, 'us-west-2')).toContain(
      'https://bedrock-agentcore.us-west-2.amazonaws.com/',
    );
  });

  it('rejects an ARN without a region', () => {
    expect(() => runtimeUrlFromArn('not-an-arn')).toThrow(/region/i);
  });

  it('rejects a malformed region', () => {
    expect(() => runtimeUrlFromArn(ARN, 'EU-CENTRAL-1')).toThrow(/region/i);
    expect(() => runtimeUrlFromArn(ARN, 'evil.example.com/')).toThrow(/region/i);
  });
});
