/**
 * AWS Client Factory
 *
 * Creates AWS SDK v3 clients authenticated via Cognito Identity Pool.
 * The Cognito JWT (ID token) is exchanged for temporary AWS credentials,
 * which are then used to instantiate any AWS service client.
 *
 * Usage:
 *   const dynamo = await getAwsClient(DynamoDBClient, { idToken, identityPoolId, userPoolId, region });
 *   const s3     = await getAwsClient(S3Client,       { idToken, identityPoolId, userPoolId, region });
 */

import { fromCognitoIdentityPool } from '@aws-sdk/credential-providers';

export interface CognitoAuthParams {
  /** Cognito ID token (JWT) from fetchAuthSession() */
  idToken: string;
  /** Cognito Identity Pool ID from amplify_outputs.json */
  identityPoolId: string;
  /** Cognito User Pool ID from amplify_outputs.json */
  userPoolId: string;
  /** AWS region — auto-detected from identityPoolId if not provided */
  region?: string;
}

/**
 * Generic AWS client factory.
 * Pass any AWS SDK v3 client constructor and Cognito auth params.
 *
 * @example
 * import { DynamoDBClient } from '@aws-sdk/client-dynamodb';
 * const client = getAwsClient(DynamoDBClient, authParams);
 *
 * @example
 * import { S3Client } from '@aws-sdk/client-s3';
 * const client = getAwsClient(S3Client, authParams);
 *
 * @example
 * import { BedrockAgentRuntimeClient } from '@aws-sdk/client-bedrock-agent-runtime';
 * const client = getAwsClient(BedrockAgentRuntimeClient, authParams);
 */
export function getAwsClient<T>(
  ClientClass: new (config: { region: string; credentials: ReturnType<typeof fromCognitoIdentityPool> }) => T,
  { idToken, identityPoolId, userPoolId, region }: CognitoAuthParams
): T {
  const resolvedRegion = region || identityPoolId.split(':')[0] || 'us-east-1';

  const credentials = fromCognitoIdentityPool({
    clientConfig: { region: resolvedRegion },
    identityPoolId,
    logins: {
      [`cognito-idp.${resolvedRegion}.amazonaws.com/${userPoolId}`]: idToken,
    },
  });

  return new ClientClass({ region: resolvedRegion, credentials });
}
