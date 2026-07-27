/**
 * Runtime invocation URL construction — the analog of the Python SDK's
 * public `build_runtime_url` (runtime/a2a.py).
 */

/** Well-formed AWS region, e.g. `us-east-1` (same pattern as the Python SDK). */
const REGION_PATTERN = /^[a-z]{2}(-[a-z]+)+-\d+$/;

/**
 * Builds the Bedrock AgentCore `InvokeAgentRuntime` HTTP URL for a runtime
 * ARN. A2A JSON-RPC payloads POSTed (SigV4-signed) to this URL are proxied
 * to the runtime container's `POST /` unmodified.
 *
 * @param runtimeArn - e.g. `arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/my-agent-abc123`
 * @param region - AWS region override; extracted from the ARN when omitted.
 * @throws Error when no valid region can be determined.
 */
export function runtimeUrlFromArn(runtimeArn: string, region?: string): string {
  // ARN format: arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>
  const resolved = region ?? runtimeArn.split(':')[3];
  if (!resolved || !REGION_PATTERN.test(resolved)) {
    throw new Error(
      `Cannot determine a valid AWS region for runtime URL (arn: ${runtimeArn}, region: ${region ?? '<from arn>'})`,
    );
  }
  return `https://bedrock-agentcore.${resolved}.amazonaws.com/runtimes/${encodeURIComponent(
    runtimeArn,
  )}/invocations/`;
}
