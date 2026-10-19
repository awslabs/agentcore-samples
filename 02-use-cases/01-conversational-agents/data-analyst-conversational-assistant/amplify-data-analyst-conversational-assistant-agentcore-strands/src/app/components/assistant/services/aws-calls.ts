// ─── AWS API Calls for the Assistant ─────────────────────────────────────────
// Query results use API route. Chart generation is direct client-side SDK call
// (same as original React app) since it needs Cognito credentials.

import { BedrockRuntimeClient, InvokeModelCommand } from '@aws-sdk/client-bedrock-runtime';
import {
  BedrockAgentCoreClient,
  ListMemoryRecordsCommand,
} from '@aws-sdk/client-bedrock-agentcore';
import { getAwsClient, type CognitoAuthParams } from '@/utils/aws-client';
import type { QueryResult, ChartData, Answer } from '../types';
import {
  extractBetweenTags,
  removeCharFromStartAndEnd,
  handleFormatter,
  CHART_PROMPT,
} from '../utils';

/**
 * Fetch query results from DynamoDB for a given prompt UUID.
 */
export const getQueryResults = async (
  queryUuid: string,
  tableName: string,
  auth: CognitoAuthParams
): Promise<QueryResult[]> => {
  const res = await fetch('/api/agent/query-results', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      idToken: auth.idToken,
      identityPoolId: auth.identityPoolId,
      userPoolId: auth.userPoolId,
      queryUuid,
      tableName,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || 'Failed to fetch query results');
  }

  const data = await res.json();
  return data.results;
};

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
    const decodedResponseBody = new TextDecoder().decode(apiResponse.body);
    const responseBody = JSON.parse(decodedResponseBody).content[0].text;

    console.log('🎨 Response chart generation:', responseBody.slice(0, 300));

    const hasChart = parseInt(extractBetweenTags(responseBody, 'has_chart'));

    if (hasChart) {
      const chartConfig = JSON.parse(
        extractBetweenTags(responseBody, 'chart_configuration')
      );
      const chart = {
        chart_type: removeCharFromStartAndEnd(
          extractBetweenTags(responseBody, 'chart_type'),
          '\n'
        ),
        chart_configuration: handleFormatter(chartConfig) as {
          options: ApexCharts.ApexOptions;
          series: ApexCharts.ApexOptions['series'];
        },
        caption: removeCharFromStartAndEnd(
          extractBetweenTags(responseBody, 'caption'),
          '\n'
        ),
      };

      console.log('🎨 Final chart:', chart.chart_type);
      return chart;
    }

    return {
      rationale: removeCharFromStartAndEnd(
        extractBetweenTags(responseBody, 'rationale'),
        '\n'
      ),
    };
  } catch (error) {
    console.error('❌ Chart generation failed:', error);
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
  const client = getAwsClient(BedrockAgentCoreClient, auth);
  const namespace = `facts/${userId}/`;

  const namespacesToTry = [
    `facts/${userId}/`,
    `/facts/${userId}/`,
    `facts/${userId}`,
    `/facts/${userId}`,
  ];

  console.log('🧠 Fetching memory facts:', { memoryId, userId, namespacesToTry });

  try {
    for (const ns of namespacesToTry) {
      console.log(`🧠 Trying namespace: "${ns}"`);
      const command = new ListMemoryRecordsCommand({
        memoryId,
        namespace: ns,
      });

      const response = await client.send(command);
      const records = response.memoryRecordSummaries || [];
      console.log(`🧠 Namespace "${ns}" returned ${records.length} records`);

      if (records.length > 0) {
        console.log('🧠 Full response:', JSON.stringify(response, null, 2));

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const facts = records.map((record: any) => ({
          memoryRecordId: record.memoryRecordId || record.id || '',
          content: record.content?.text || JSON.stringify(record.content) || '',
          createdAt: record.createdAt || record.createdTimestamp || '',
          namespace: record.namespace || ns,
        }));

        facts.forEach((fact: MemoryFact, i: number) => {
          console.log(`🧠 Fact ${i + 1}:`, fact.content.slice(0, 100));
        });

        return facts;
      }
    }

    console.log('🧠 No facts found in any namespace pattern');
    return [];
  } catch (error) {
    console.error('❌ Failed to fetch memory facts:', { memoryId, error });
    return [];
  }
};
