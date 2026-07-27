import { withApiKey } from 'bedrock-agentcore/identity';

/**
 * Identity quick win (pain #7): outbound credentials come from AgentCore
 * Identity instead of hand-rolled env-var plumbing.
 *
 * The scenario: after composing a triage summary, the lead "notifies" an
 * incident channel through a (mocked) third-party API that needs an API
 * key. `withApiKey` wraps the call so the key is fetched from the
 * AgentCore Identity credential provider at call time and injected as the
 * last argument — the handler code never touches credential storage.
 *
 * Deployed: requires an ApiKeyCredentialProvider (created by the CDK
 * stack) and the workload access token that AgentCore Runtime hands each
 * request (context.workloadAccessToken). Local: no workload token exists,
 * so the notification falls back to a log line — the sample stays fully
 * runnable without AWS.
 */

const PROVIDER_NAME = process.env.NOTIFY_CREDENTIAL_PROVIDER ?? 'sample-incident-notifier';

async function postNotification(summary: string, apiKey: string): Promise<string> {
  // A real integration would POST to the incident tool here. The mock
  // proves the credential arrived without shipping a fake HTTP call.
  console.log(
    `[lead] notify: would deliver triage summary (${summary.length} chars) ` +
      `using API key ending "…${apiKey.slice(-4)}"`,
  );
  return 'notified';
}

export async function notifyIncidentChannel(
  summary: string,
  workloadAccessToken: string | undefined,
): Promise<string> {
  if (!workloadAccessToken) {
    console.log('[lead] notify: no workload access token (local mode) — skipping Identity call');
    return 'skipped (local mode)';
  }

  const notify = withApiKey({
    workloadIdentityToken: workloadAccessToken,
    providerName: PROVIDER_NAME,
  })(postNotification);

  try {
    return await notify(summary);
  } catch (error) {
    // Notification is best-effort; triage answers must not fail on it.
    console.warn(`[lead] notify failed: ${error instanceof Error ? error.message : error}`);
    return 'failed';
  }
}
