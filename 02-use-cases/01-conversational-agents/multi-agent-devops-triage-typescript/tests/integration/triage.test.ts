import { spawn, type ChildProcess } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

/**
 * Phase 1 integration test: lead → both workers (A2A) → mock Gateway tool →
 * composed triage answer, all running locally against real Bedrock models.
 *
 * Requires AWS credentials with Bedrock access. Model/region come from
 * ANTHROPIC_MODEL / AWS_REGION (defaults below use us-east-1 + Haiku 4.5
 * to keep the test fast and cheap).
 */

const REGION = process.env.AWS_REGION ?? 'us-east-1';
const MODEL = process.env.ANTHROPIC_MODEL ?? 'global.anthropic.claude-haiku-4-5-20251001-v1:0';

const MOCK_CATALOG_PORT = 8900;
const LOG_ANALYST_PORT = 9001;
const RUNBOOK_PORT = 9002;
const LEAD_PORT = 8080;

const REPO_ROOT = new URL('../..', import.meta.url).pathname;

interface ManagedProcess {
  name: string;
  child: ChildProcess;
  output: string[];
}

const processes: ManagedProcess[] = [];

function start(name: string, entrypoint: string, env: Record<string, string>): ManagedProcess {
  const child = spawn('npx', ['tsx', entrypoint], {
    cwd: REPO_ROOT,
    env: { ...process.env, AWS_REGION: REGION, ANTHROPIC_MODEL: MODEL, ...env },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  const managed: ManagedProcess = { name, child, output: [] };
  child.stdout?.on('data', (chunk: Buffer) => managed.output.push(chunk.toString()));
  child.stderr?.on('data', (chunk: Buffer) => managed.output.push(chunk.toString()));
  processes.push(managed);
  return managed;
}

async function waitForHealthy(url: string, name: string, timeoutMs = 30_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  const managed = processes.find((p) => p.name === name);
  throw new Error(
    `${name} did not become healthy at ${url}.\nOutput:\n${managed?.output.join('') ?? '(none)'}`,
  );
}

beforeAll(async () => {
  start('mock-catalog', 'scripts/mock-service-catalog/src/main.ts', {
    PORT: String(MOCK_CATALOG_PORT),
  });
  start('log-analyst', 'agents/log-analyst/src/main.ts', {
    PORT: String(LOG_ANALYST_PORT),
  });
  start('runbook', 'agents/runbook/src/main.ts', {
    PORT: String(RUNBOOK_PORT),
    SERVICE_CATALOG_MCP_URL: `http://localhost:${MOCK_CATALOG_PORT}/mcp`,
  });
  start('lead', 'agents/lead/src/main.ts', {
    PORT: String(LEAD_PORT),
    LOG_ANALYST_URL: `http://localhost:${LOG_ANALYST_PORT}`,
    RUNBOOK_URL: `http://localhost:${RUNBOOK_PORT}`,
  });

  await Promise.all([
    waitForHealthy(`http://localhost:${MOCK_CATALOG_PORT}/ping`, 'mock-catalog'),
    waitForHealthy(`http://localhost:${LOG_ANALYST_PORT}/ping`, 'log-analyst'),
    waitForHealthy(`http://localhost:${RUNBOOK_PORT}/ping`, 'runbook'),
    waitForHealthy(`http://localhost:${LEAD_PORT}/ping`, 'lead'),
  ]);
});

afterAll(() => {
  for (const { child } of processes) {
    child.kill('SIGTERM');
  }
});

describe('incident triage end-to-end (local)', () => {
  it('serves the AgentCore A2A contract on both workers', async () => {
    for (const port of [LOG_ANALYST_PORT, RUNBOOK_PORT]) {
      const ping = await fetch(`http://localhost:${port}/ping`);
      expect(ping.status).toBe(200);
      expect(await ping.json()).toMatchObject({ status: 'Healthy' });

      const cardRes = await fetch(`http://localhost:${port}/.well-known/agent-card.json`);
      expect(cardRes.status).toBe(200);
      const card = (await cardRes.json()) as { name: string; url?: string };
      expect(card.name).toBeTruthy();
    }
  });

  it(
    'answers a triage query through A2A workers and the Gateway tool',
    { timeout: 600_000 },
    async () => {
      const res = await fetch(`http://localhost:${LEAD_PORT}/invocations`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-amzn-bedrock-agentcore-runtime-session-id': randomUUID(),
        },
        body: JSON.stringify({
          prompt:
            'API latency on orders-api spiked after the 14:00 deploy. ' +
            'Logs show "ERROR timeout connecting to postgres-orders" repeated 40 times starting 14:02, ' +
            'p99 latency went from 180ms to 2400ms. What happened and what should we do?',
        }),
      });

      expect(res.status).toBe(200);
      const body = (await res.json()) as { answer: string };
      const answer = body.answer.toLowerCase();

      // Composition evidence: ownership facts can only come from the runbook
      // worker's catalog tool, and the cause analysis from the log analyst.
      expect(answer).toContain('commerce');
      expect(answer).toMatch(/postgres|connection|database/);
      expect(answer).toMatch(/roll\s?back|rollback|scale/);

      // The lead must have actually delegated over A2A to both workers.
      const leadOutput = processes.find((p) => p.name === 'lead')?.output.join('') ?? '';
      expect(leadOutput).toContain('log-analyst update');

      const runbookOutput = processes.find((p) => p.name === 'runbook')?.output.join('') ?? '';
      expect(runbookOutput).toContain('[a2a] runbook listening');
    },
  );
});
