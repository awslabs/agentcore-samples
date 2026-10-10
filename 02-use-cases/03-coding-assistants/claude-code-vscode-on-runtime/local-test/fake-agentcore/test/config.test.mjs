// Configuration the control plane would reject is rejected at start, and the policy evaluator. No ports.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateAuthorizerConfig } from '../lib/authorizer.mjs';
import { validateAllowlist } from '../lib/headers.mjs';
import { readFileSync } from 'node:fs';
import { evaluatePolicy, boxPolicy, d2Policy, ACTIONS } from '../lib/policy.mjs';
import { authorizerFor } from '../../config/stack.mjs';

const base = () => authorizerFor({ issuer: 'http://localhost:9400/oauth2/default', clientId: '0oadevboxspafake0001', ownerUid: '00uadalovelace000001' });

test('the runtime authorizer is valid', () => {
  assert.doesNotThrow(() => validateAuthorizerConfig(base()));
});

test('authorizer rules the service enforces', () => {
  const bad = (mutate, pattern) => {
    const cfg = base();
    mutate(cfg.customJWTAuthorizer);
    assert.throws(() => validateAuthorizerConfig(cfg), pattern);
  };
  bad((a) => { a.discoveryUrl = 'http://localhost:9400/oauth2/default'; }, /well-known/);
  bad((a) => { delete a.allowedAudience; delete a.allowedClients; delete a.allowedScopes; delete a.customClaims; }, /at least one/);
  bad((a) => { a.customClaims[1].inboundTokenClaimName = 'client_id'; }, /reserved/);
  bad((a) => { a.customClaims[1].inboundTokenClaimName = 'devbox-uid'; }, /invalid inboundTokenClaimName/);
  bad((a) => { a.customClaims[0].authorizingClaimMatchValue.claimMatchValue.matchValueStringList = ['devbox users']; }, /does not match/);
  bad((a) => { a.customClaims[0].authorizingClaimMatchValue.claimMatchOperator = 'CONTAINS'; }, /exactly one matchValueString/);
  bad((a) => { a.customClaims[1].authorizingClaimMatchValue.claimMatchOperator = 'CONTAINS'; }, /STRING supports only EQUALS/);
});

test('header allowlist rules', () => {
  assert.deepEqual([...validateAllowlist(['Authorization', 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath'], { hasJwtAuthorizer: true })],
    ['authorization', 'x-amzn-bedrock-agentcore-runtime-custom-vscodepath']);
  assert.throws(() => validateAllowlist(['Authorization'], { hasJwtAuthorizer: false }), /customJWTAuthorizer/);
  for (const h of ['Origin', 'Cookie', 'If-None-Match', 'Accept-Encoding', 'Sec-WebSocket-Protocol', 'X-Forwarded-For']) {
    assert.throws(() => validateAllowlist([h], { hasJwtAuthorizer: true }), /restricted/, h);
  }
  assert.throws(() => validateAllowlist(['x-amz-date'], { hasJwtAuthorizer: true }), /reserved/);
  assert.throws(() => validateAllowlist(['x-amzn-trace-id'], { hasJwtAuthorizer: true }), /reserved/);
  assert.throws(() => validateAllowlist(['X-A', 'x-a'], { hasJwtAuthorizer: true }), /duplicate/);
  assert.throws(() => validateAllowlist(Array.from({ length: 21 }, (_, i) => `x-h${i}`), { hasJwtAuthorizer: true }), /at most 20/);
});

test('policy evaluation: explicit deny wins, a policy needs explicit allows, no policy allows', () => {
  const arn = 'arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/devbox_ada-L0calTest1';
  const p = d2Policy(arn);
  assert.equal(evaluatePolicy(p, ACTIONS.invoke, arn), 'allow');
  assert.equal(evaluatePolicy(p, ACTIONS.ws, arn), 'allow');
  for (const a of [ACTIONS.command, ACTIONS.shell, ACTIONS.stop]) assert.equal(evaluatePolicy(p, a, arn), 'explicit-deny');
  assert.equal(evaluatePolicy(p, ACTIONS.invoke, arn.replace('ada', 'grace')), 'implicit-deny');
  assert.equal(evaluatePolicy(null, ACTIONS.command, arn), 'allow');
  const wildcard = { Statement: [{ Effect: 'Allow', Principal: { AWS: '*' }, Action: 'bedrock-agentcore:Invoke*', Resource: '*' }] };
  assert.equal(evaluatePolicy(wildcard, ACTIONS.ws, arn), 'allow');
  const namedPrincipal = { Statement: [{ Effect: 'Allow', Principal: { AWS: 'arn:aws:iam::111122223333:root' }, Action: '*', Resource: '*' }] };
  assert.equal(evaluatePolicy(namedPrincipal, ACTIONS.invoke, arn), 'implicit-deny', 'OAuth callers are Principal "*"');
});

test('the microVM box policy (what deploy sets): invoke, /ws and the terminal; no command API or stop', () => {
  const arn = 'arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/devbox_vm_ada-L0calTest1';
  const p = boxPolicy(arn);
  for (const a of [ACTIONS.invoke, ACTIONS.ws, ACTIONS.shell]) assert.equal(evaluatePolicy(p, a, arn), 'allow', a);
  for (const a of [ACTIONS.command, ACTIONS.stop, 'bedrock-agentcore:InvokeAgentRuntimeForUser',
    'bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser']) assert.equal(evaluatePolicy(p, a, arn), 'explicit-deny', a);
  assert.equal(evaluatePolicy(p, ACTIONS.shell, arn.replace('ada', 'grace')), 'implicit-deny', 'only this runtime');
  const deployed = JSON.parse(readFileSync(new URL('../../../deploy/templates/iam/runtime-resource-policy.json', import.meta.url), 'utf8')
    .replaceAll('{{RUNTIME_ARN}}', arn));
  assert.deepEqual(p, deployed, 'the same document deploy puts on each runtime');
});
