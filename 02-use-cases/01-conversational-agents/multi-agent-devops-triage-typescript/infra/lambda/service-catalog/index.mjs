/**
 * Mock service-catalog Lambda — the AgentCore Gateway target.
 *
 * Gateway invokes this function with the MCP tool arguments as the event
 * and the tool name in context.clientContext.Custom.bedrockAgentCoreToolName
 * (prefixed "<targetName>___"). Same fictional data as
 * scripts/mock-service-catalog, so local and deployed answers match.
 */

const SERVICES = {
  'orders-api': {
    service: 'orders-api',
    owningTeam: 'Commerce Platform',
    escalationContact: 'commerce-oncall@example.com',
    tier: 'tier-1',
    dependencies: ['payments-svc', 'inventory-svc', 'postgres-orders'],
  },
  'payments-svc': {
    service: 'payments-svc',
    owningTeam: 'Payments',
    escalationContact: 'payments-oncall@example.com',
    tier: 'tier-1',
    dependencies: ['postgres-payments'],
  },
  'inventory-svc': {
    service: 'inventory-svc',
    owningTeam: 'Supply Chain',
    escalationContact: 'supply-oncall@example.com',
    tier: 'tier-2',
    dependencies: ['redis-inventory'],
  },
};

const RUNBOOKS = [
  {
    service: 'orders-api',
    symptom: 'latency',
    steps: [
      'Check p99 latency dashboard for orders-api and compare with the deploy marker.',
      'Inspect connection pool saturation on postgres-orders (max_connections, wait events).',
      'If the spike correlates with a deploy, roll back via the standard pipeline (deploy tool, "rollback" action).',
      'If rollback is not possible, scale the orders-api ASG by +2 instances and enable request shedding for non-critical endpoints.',
      'Page the Commerce Platform on-call if latency does not recover within 15 minutes.',
    ],
  },
  {
    service: 'orders-api',
    symptom: 'errors',
    steps: [
      'Check error-rate dashboard, split by endpoint and status code.',
      'Correlate with recent config or deploy changes.',
      'Roll back the most recent change if it correlates.',
    ],
  },
  {
    service: 'payments-svc',
    symptom: 'latency',
    steps: [
      'Check payment provider status page for upstream degradation.',
      'Verify circuit breaker state on the provider client.',
    ],
  },
];

export const handler = async (event, context) => {
  // Node runtime exposes client context as context.clientContext.Custom
  // (Java uses .custom); handle both shapes defensively.
  const custom =
    context.clientContext?.Custom ?? context.clientContext?.custom ?? {};
  let toolName = custom.bedrockAgentCoreToolName ?? '';
  const delimiter = '___';
  if (toolName.includes(delimiter)) {
    toolName = toolName.slice(toolName.indexOf(delimiter) + delimiter.length);
  }

  if (toolName === 'lookup_service') {
    const record = SERVICES[String(event.service ?? '').trim().toLowerCase()];
    if (!record) {
      return `Service "${event.service}" not found. Known services: ${Object.keys(SERVICES).join(', ')}`;
    }
    return JSON.stringify(record, null, 2);
  }

  if (toolName === 'get_runbook') {
    const service = String(event.service ?? '').trim().toLowerCase();
    const symptom = String(event.symptom ?? '').trim().toLowerCase();
    const runbook =
      RUNBOOKS.find((r) => r.service === service && symptom.includes(r.symptom)) ??
      RUNBOOKS.find((r) => r.service === service);
    if (!runbook) {
      return `No runbook found for service "${event.service}".`;
    }
    return JSON.stringify(runbook, null, 2);
  }

  throw new Error(`Unknown tool: ${custom.bedrockAgentCoreToolName ?? '(none)'}`);
};
