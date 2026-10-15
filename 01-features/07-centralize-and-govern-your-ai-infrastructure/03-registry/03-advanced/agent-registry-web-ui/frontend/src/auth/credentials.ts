// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { fromCognitoIdentityPool } from "@aws-sdk/credential-provider-cognito-identity";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import { config, cognitoLoginKey } from "../config";

/**
 * Build an AWS credential provider that exchanges the User Pool ID token for
 * short-lived, persona-scoped IAM credentials via the Cognito Identity Pool.
 * The Identity Pool is configured for role-based access control ("choose role
 * from token"), so the returned credentials carry the IAM role mapped to the
 * user's Cognito group (Consumer / Publisher / Approver).
 *
 * The provider is memoized by SDK clients and re-vends on expiry automatically.
 */
export function credentialProvider(
  idToken: string,
): AwsCredentialIdentityProvider {
  return fromCognitoIdentityPool({
    clientConfig: { region: config.region },
    identityPoolId: config.identityPoolId,
    logins: {
      [cognitoLoginKey]: idToken,
    },
  });
}
