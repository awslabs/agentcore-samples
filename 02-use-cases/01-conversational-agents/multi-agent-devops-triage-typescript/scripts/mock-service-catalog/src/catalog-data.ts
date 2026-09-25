/**
 * Fictional service-catalog data. In deployed mode (Phase 2) the same
 * lookups are served by a Lambda behind AgentCore Gateway.
 */

export interface ServiceRecord {
  service: string;
  owningTeam: string;
  escalationContact: string;
  tier: string;
  dependencies: string[];
}

export interface RunbookRecord {
  service: string;
  symptom: string;
  steps: string[];
}

export const SERVICES: Record<string, ServiceRecord> = {
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

export const RUNBOOKS: RunbookRecord[] = [
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

export function lookupService(name: string): ServiceRecord | undefined {
  return SERVICES[name.trim().toLowerCase()];
}

export function findRunbook(service: string, symptom: string): RunbookRecord | undefined {
  const normalizedService = service.trim().toLowerCase();
  const normalizedSymptom = symptom.trim().toLowerCase();
  return (
    RUNBOOKS.find(
      (r) => r.service === normalizedService && normalizedSymptom.includes(r.symptom),
    ) ?? RUNBOOKS.find((r) => r.service === normalizedService)
  );
}
