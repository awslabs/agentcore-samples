// ─── Query Results from DynamoDB ─────────────────────────────────────────────
// POST /api/agent/query-results
// Accepts: { idToken, identityPoolId, userPoolId, queryUuid, tableName }
// Returns: { results: QueryResult[] }

import { NextRequest, NextResponse } from 'next/server';
import { DynamoDBClient, QueryCommand } from '@aws-sdk/client-dynamodb';
import { getAwsClient } from '@/utils/aws-client';

export async function POST(req: NextRequest) {
  try {
    const { idToken, identityPoolId, userPoolId, queryUuid, tableName } = await req.json();

    if (!idToken || !identityPoolId || !userPoolId) {
      return NextResponse.json({ error: 'Missing required auth parameters' }, { status: 400 });
    }

    if (!queryUuid || !tableName) {
      return NextResponse.json({ error: 'Missing queryUuid or tableName' }, { status: 400 });
    }

    const dynamodb = getAwsClient(DynamoDBClient, { idToken, identityPoolId, userPoolId });

    const command = new QueryCommand({
      TableName: tableName,
      KeyConditionExpression: 'id = :queryUuid',
      ExpressionAttributeValues: { ':queryUuid': { S: queryUuid } },
      ConsistentRead: true,
    });

    const response = await dynamodb.send(command);
    const results = (response.Items || []).map((item) => ({
      query: item.sql_query?.S || '',
      query_results: JSON.parse(item.data?.S || '{}').result || [],
      query_description: item.sql_query_description?.S || '',
    }));

    return NextResponse.json({ results });
  } catch (err: unknown) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
