// ─── AWS API Calls for the Assistant ─────────────────────────────────────────
// Query results use API route. Chart generation is direct client-side SDK call
// (same as original React app) since it needs Cognito credentials.

import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime';
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from '@aws-sdk/client-bedrock-agentcore';
import { getAwsClient, type CognitoAuthParams } from '@/utils/aws-client';
import { RuntimeEvents } from './runtime-events';
import type { QueryResult, ChartData, Answer } from '../types';
import { CHART_PROMPT } from '../utils';
import { parseChartResponse } from '../chart-contract';

/**
 * Fetch query results from DynamoDB for a given prompt UUID.
 */
export const getQueryResults = async (
  queryUuid: string,
  tableName: string,
  auth: CognitoAuthParams
): Promise<QueryResult[]> => {
  const data = await runtimeRead({ operation: 'query_results', prompt_uuid: queryUuid }, auth);
  return data.results as QueryResult[];
};

async function runtimeRead(payload: Record<string, string>, auth: CognitoAuthParams) {
  const arn = process.env.AGENT_RUNTIME_ARN;
  if (!arn) throw new Error('AGENT_RUNTIME_ARN is required');
  const session = crypto.randomUUID();
  const response = await getAwsClient(BedrockAgentCoreClient, auth).send(new InvokeAgentRuntimeCommand({
    agentRuntimeArn: arn, qualifier: process.env.AGENT_ENDPOINT_NAME || 'DEFAULT',
    runtimeSessionId: session,
    payload: JSON.stringify({ ...payload, session_id: session, id_token: auth.idToken }),
  }));
  if (!response.response) throw new Error('Empty Runtime response');
  const reader = response.response.transformToWebStream().getReader();
  const decoder = new TextDecoder();
  const parser = new RuntimeEvents();
  const values: Record<string, unknown>[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      values.push(...parser.push(decoder.decode(value, { stream: true })));
    }
    values.push(...parser.push(decoder.decode()));
    parser.finish();
  } finally { reader.releaseLock(); }
  if (values.length !== 1) throw new Error('Invalid Runtime read response');
  return values[0];
}

/**
 * Generate a chart configuration using Bedrock (Claude).
 * Direct client-side SDK call — same pattern as the original React app.
 */
export const generateChart = async (
  answer: Answer,
  modelId: string,
  auth: CognitoAuthParams
): Promise<ChartData> => {
  const bedrock = getAwsClient(BedrockRuntimeClient, auth);

  let queryResultsStr = '';
  for (let i = 0; i < (answer.queryResults?.length || 0); i++) {
    queryResultsStr += JSON.stringify(answer.queryResults![i].query_results) + '\n';
  }

  // Build text content from answer.text array — same as original
  const textContent = answer.text
    ?.filter((t) => t.type === 'text')
    .map((t) => ('content' in t ? t.content : ''))
    .join('\n') || '';

  const prompt = CHART_PROMPT
    .replace(/<<answer>>/i, textContent)
    .replace(/<<data_sources>>/i, queryResultsStr);

  const payload = {
    anthropic_version: 'bedrock-2023-05-31',
    max_tokens: 2000,
    temperature: 1,
    messages: [
      {
        role: 'user',
        content: [{ type: 'text', text: prompt }],
      },
    ],
  };

  try {
    console.log('🎨 Request chart generation, model:', modelId);

    const command = new InvokeModelCommand({
      contentType: 'application/json',
      body: JSON.stringify(payload),
      modelId,
    });

    const apiResponse = await bedrock.send(command);
    if (!apiResponse.body || apiResponse.body.byteLength > 200_000) {
      throw new Error('Chart response is missing or too large');
    }
    const decodedResponseBody = new TextDecoder().decode(apiResponse.body);
    const responseBody: unknown = JSON.parse(decodedResponseBody)?.content?.[0]?.text;
    return parseChartResponse(responseBody);
  } catch {
    console.error('Chart generation failed or returned unsupported data');
    return {
      rationale: 'Error generating or parsing chart data.',
    };
  }
};


export interface MemoryFact {
  memoryRecordId: string;
  content: string;
  createdAt: string;
  namespace: string;
}

/**
 * Fetch long-term memory facts for the current user from AgentCore Memory.
 */
export const getMemoryFacts = async (
  memoryId: string,
  userId: string,
  auth: CognitoAuthParams
): Promise<MemoryFact[]> => {
  const data = await runtimeRead({ operation: 'memory_facts' }, auth);
  return data.facts as MemoryFact[];
};
