// Runtime resource-based policy evaluation, reduced to what OAuth callers hit: they are evaluated as
// Principal "*", an explicit Deny always wins, and once a policy exists an action needs an explicit Allow.
export const ACTIONS = {
  invoke: 'bedrock-agentcore:InvokeAgentRuntime',
  ws: 'bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream',
  command: 'bedrock-agentcore:InvokeAgentRuntimeCommand',
  shell: 'bedrock-agentcore:InvokeAgentRuntimeCommandShell',
  stop: 'bedrock-agentcore:StopRuntimeSession',
};

// The original policy: the browser may invoke and open /ws; nothing else.
export function d2Policy(runtimeArn) {
  return {
    Version: '2012-10-17',
    Statement: [
      {
        Sid: 'DenyShellsAndStop',
        Effect: 'Deny',
        Principal: '*',
        Action: [
          ACTIONS.command, ACTIONS.shell, ACTIONS.stop,
          'bedrock-agentcore:InvokeAgentRuntimeForUser',
          'bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser',
        ],
        Resource: runtimeArn,
      },
      { Sid: 'AllowInvoke', Effect: 'Allow', Principal: '*', Action: [ACTIONS.invoke, ACTIONS.ws], Resource: runtimeArn },
    ],
  };
}

// The policy deploy sets on the microVM boxes (deploy/templates/iam/runtime-resource-policy.json):
// the owner's browser may also open AgentCore's terminal; the command API, stop and the
// act-as-user actions stay denied for everyone. The local stack renders this one.
export function boxPolicy(runtimeArn) {
  return {
    Version: '2012-10-17',
    Statement: [
      {
        Sid: 'TheOwnersBrowserMayUseTheBoxAndItsTerminal',
        Effect: 'Allow',
        Principal: '*',
        Action: [ACTIONS.invoke, ACTIONS.ws, ACTIONS.shell],
        Resource: runtimeArn,
      },
      {
        Sid: 'NoCommandApiNoStopNoActingAsSomeoneElse',
        Effect: 'Deny',
        Principal: '*',
        Action: [
          ACTIONS.command, ACTIONS.stop,
          'bedrock-agentcore:InvokeAgentRuntimeForUser',
          'bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStreamForUser',
        ],
        Resource: runtimeArn,
      },
    ],
  };
}

const asList = (v) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);

function glob(pattern, value, caseInsensitive) {
  const re = new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`, caseInsensitive ? 'i' : '');
  return re.test(value);
}

function appliesToAnonymous(principal) {
  return principal === '*' || asList(principal?.AWS).includes('*');
}

function matches(statement, action, resource) {
  if (!appliesToAnonymous(statement.Principal)) return false;
  const actionHit = statement.NotAction
    ? !asList(statement.NotAction).some((a) => glob(a, action, true))
    : asList(statement.Action).some((a) => glob(a, action, true));
  const resourceHit = statement.NotResource
    ? !asList(statement.NotResource).some((r) => glob(r, resource, false))
    : asList(statement.Resource ?? '*').some((r) => glob(r, resource, false));
  return actionHit && resourceHit;
}

// Returns 'allow' | 'explicit-deny' | 'implicit-deny'. No policy at all means allow.
export function evaluatePolicy(policy, action, resource) {
  if (!policy) return 'allow';
  const statements = asList(policy.Statement);
  if (statements.some((s) => s.Effect === 'Deny' && matches(s, action, resource))) return 'explicit-deny';
  if (statements.some((s) => s.Effect === 'Allow' && matches(s, action, resource))) return 'allow';
  return 'implicit-deny';
}
