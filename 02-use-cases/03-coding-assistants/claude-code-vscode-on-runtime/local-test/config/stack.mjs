// The local stack's settings in one place: ports, the fake Okta directory, the runtime authorizer,
// the browser config and the box env. `node config/stack.mjs render <dir>`
// writes the generated files that compose.yaml and edge-local read.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sessionIdFor, boxKeyFor } from '../lib/ids.mjs';
import { boxPolicy, d2Policy } from '../fake-agentcore/lib/policy.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const directory = JSON.parse(readFileSync(path.join(HERE, '..', 'fake-okta', 'directory.json'), 'utf8'));

// The default ports, overridable (OKTA_PORT, ...) for laptops where one of them is taken; run.sh picks them.
const port = (name, fallback) => Number(process.env[name] || fallback);
export const PORTS = {
  okta: port('OKTA_PORT', 9400), agentcore: port('AGENTCORE_PORT', 9401),
  workbench: port('WORKBENCH_PORT', 9402), webview: port('WEBVIEW_PORT', 9403),
};
export const ORIGINS = Object.fromEntries(Object.entries(PORTS).map(([k, p]) => [k, `http://localhost:${p}`]));
export const ISSUER = `${ORIGINS.okta}/oauth2/default`;
export const SPA_CLIENT_ID = '0oadevboxspafake0001';
export const OTHER_CLIENT_ID = '0oaotherappfake00002';
export const SCOPES = 'openid profile email offline_access devbox';
export const ACCOUNT_ID = '111122223333';
export const REGION = 'us-east-1';
export const COMMIT = '072586267e68ece9a47aa43f8c108e0dcbf44622';
export const SERVER_ROOT = `/stable-${COMMIT}`;
export const HEADER_ALLOWLIST = ['Authorization', 'X-Amzn-Bedrock-AgentCore-Runtime-Custom-Vscodepath'];
export const USERS = directory.users;

// One box in the local stack: Ada's (Power tier), generation 1.
export const BOXES = [
  { name: 'ada', tier: 'Power', generation: 1, runtimeArn: `arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/devbox_ada-L0calTest1`, boxUrl: 'http://box:8080' },
];

export const MODELS = {
  Power: {
    availableModels: ['opus', 'sonnet', 'haiku'],
    ANTHROPIC_DEFAULT_OPUS_MODEL: 'us.anthropic.claude-opus-5',
    ANTHROPIC_DEFAULT_SONNET_MODEL: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
    ANTHROPIC_DEFAULT_HAIKU_MODEL: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
  },
  Standard: {
    availableModels: ['sonnet', 'haiku'],
    ANTHROPIC_DEFAULT_SONNET_MODEL: 'us.anthropic.claude-sonnet-4-5-20250929-v1:0',
    ANTHROPIC_DEFAULT_HAIKU_MODEL: 'us.anthropic.claude-haiku-4-5-20251001-v1:0',
  },
};

// The runtime authorizer, as deploy sets it apart from the issuer and ids.
export function authorizerFor({ issuer = ISSUER, clientId = SPA_CLIENT_ID, ownerUid }) {
  return {
    customJWTAuthorizer: {
      discoveryUrl: `${issuer}/.well-known/openid-configuration`,
      allowedAudience: ['api://default'],
      allowedClients: [clientId],
      allowedScopes: ['devbox'],
      customClaims: [
        {
          inboundTokenClaimName: 'groups', inboundTokenClaimValueType: 'STRING_ARRAY',
          authorizingClaimMatchValue: { claimMatchValue: { matchValueStringList: ['devbox-users'] }, claimMatchOperator: 'CONTAINS_ANY' },
        },
        {
          inboundTokenClaimName: 'uid', inboundTokenClaimValueType: 'STRING',
          authorizingClaimMatchValue: { claimMatchValue: { matchValueString: ownerUid }, claimMatchOperator: 'EQUALS' },
        },
      ],
    },
  };
}

export function runtimeEntry({ runtimeArn, boxUrl, ownerUid, issuer = ISSUER, clientId = SPA_CLIENT_ID, allowlist = HEADER_ALLOWLIST, resourcePolicy = 'd2' }) {
  return {
    agentRuntimeArn: runtimeArn,
    boxUrl,
    authorizerConfiguration: authorizerFor({ issuer, clientId, ownerUid }),
    requestHeaderConfiguration: { requestHeaderAllowlist: allowlist },
    resourcePolicy: resourcePolicy === 'd2' ? d2Policy(runtimeArn) : resourcePolicy === 'box' ? boxPolicy(runtimeArn) : resourcePolicy,
  };
}

export function runtimesConfig({ oktaFetchBase } = {}) {
  return {
    settings: { publicBase: ORIGINS.agentcore },
    // Inside Docker, localhost:9400 is not fake Okta; fetch discovery/JWKS by service name, keep iss public.
    oidcFetchRewrite: oktaFetchBase ? [{ from: ORIGINS.okta, to: oktaFetchBase }] : [],
    // The policy deploy puts on a microVM box (the terminal allowed). The fake doesn't emulate the
    // terminal, so an allowed /ws/shells answers 501; the unit tests keep the original policy, without the terminal (runtimeEntry's default).
    runtimes: BOXES.map((b) => runtimeEntry({ runtimeArn: b.runtimeArn, boxUrl: b.boxUrl, ownerUid: USERS[b.name].uid, resourcePolicy: 'box' })),
  };
}

// /devbox-config.json, pointed at the fakes, in the shape deploy writes. deploy says
// compute "microvm" and terminal true; this box is a local container and fake AgentCore has no
// terminal (/ws/shells), so the /terminal page says so instead of trying.
export function devboxConfig() {
  return {
    region: REGION,
    commit: COMMIT,
    serverRoot: SERVER_ROOT,
    agentcoreBase: ORIGINS.agentcore,
    okta: { issuer: ISSUER, clientId: SPA_CLIENT_ID, scopes: SCOPES },
    webviewOrigin: ORIGINS.webview,
    boxes: Object.fromEntries(BOXES.map((b) => [boxKeyFor(USERS[b.name].uid), { name: b.name, runtimeArn: b.runtimeArn, generation: b.generation, compute: 'local', terminal: false }])),
  };
}

// The runtime env for a box.
export function boxEnv(box, { mountDelaySeconds = 5 } = {}) {
  const user = USERS[box.name];
  return {
    DEVBOX_OWNER: box.name,
    DEVBOX_OWNER_UID: user.uid,
    DEVBOX_SESSION_ID: sessionIdFor(user.uid, box.generation),
    DEVBOX_TIER: box.tier,
    DEVBOX_SSO_ROLE: `ClaudeCode-${box.tier}`,
    DEVBOX_ACCOUNT_ID: ACCOUNT_ID,
    DEVBOX_SSO_START_URL: 'https://d-0000000000.awsapps.com/start',
    DEVBOX_SSO_REGION: REGION,
    DEVBOX_MODELS: JSON.stringify(MODELS[box.tier]),
    DEVBOX_TOOLS_GATEWAY_URL: `https://devbox-tools-0000000000.gateway.bedrock-agentcore.${REGION}.amazonaws.com/mcp`,
    DEVBOX_TEST_MOUNT_DELAY: String(mountDelaySeconds),
  };
}

const envFile = (obj) => Object.entries(obj).map(([k, v]) => `${k}=${v}`).join('\n') + '\n';

export function render(outDir) {
  mkdirSync(outDir, { recursive: true });
  const cfg = devboxConfig();
  writeFileSync(path.join(outDir, 'devbox-config.json'), `${JSON.stringify(cfg, null, 2)}\n`);
  writeFileSync(path.join(outDir, 'runtimes.json'), `${JSON.stringify(runtimesConfig({ oktaFetchBase: 'http://okta:9400' }), null, 2)}\n`);
  const mountDelaySeconds = Number(process.env.DEVBOX_TEST_MOUNT_DELAY ?? 5);
  writeFileSync(path.join(outDir, 'box.env'), envFile(boxEnv(BOXES[0], { mountDelaySeconds })));
  writeFileSync(path.join(outDir, 'edge.env'), envFile({
    DEVBOX_CONFIG_JSON: JSON.stringify(cfg),
    WORKBENCH_ORIGIN: ORIGINS.workbench,
    WEBVIEW_ORIGIN: ORIGINS.webview,
  }));
  return outDir;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [cmd, out] = process.argv.slice(2);
  if (cmd === 'render') console.log(`rendered ${render(path.resolve(out ?? path.join(HERE, '..', 'generated')))}`);
  else if (cmd === 'session-id') console.log(sessionIdFor(USERS[out ?? 'ada'].uid, 1));
  else { console.error('usage: node config/stack.mjs render [dir] | session-id [user]'); process.exit(2); }
}
