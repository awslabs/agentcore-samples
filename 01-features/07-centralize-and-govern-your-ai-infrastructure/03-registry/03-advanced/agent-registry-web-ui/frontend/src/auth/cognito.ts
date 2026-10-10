// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  CognitoUserPool,
  CognitoUser,
  AuthenticationDetails,
  CognitoUserSession,
} from "amazon-cognito-identity-js";
import { config } from "../config";

const userPool = new CognitoUserPool({
  UserPoolId: config.userPoolId,
  ClientId: config.userPoolClientId,
});

export interface AuthSession {
  idToken: string;
  accessToken: string;
  email: string;
  groups: string[];
}

function sessionToAuth(session: CognitoUserSession): AuthSession {
  const idToken = session.getIdToken();
  const payload = idToken.decodePayload() as Record<string, unknown>;
  return {
    idToken: idToken.getJwtToken(),
    accessToken: session.getAccessToken().getJwtToken(),
    email:
      (payload["email"] as string) ??
      (payload["cognito:username"] as string) ??
      "",
    groups: (payload["cognito:groups"] as string[]) ?? [],
  };
}

/**
 * Sign in with email + password (USER_PASSWORD_AUTH).
 * If the account requires a new password (admin-created users' first login),
 * `onNewPassword` is invoked; return the new password from it to complete.
 */
export function signIn(
  email: string,
  password: string,
  onNewPassword?: () => Promise<string>,
): Promise<AuthSession> {
  const user = new CognitoUser({ Username: email, Pool: userPool });
  const details = new AuthenticationDetails({
    Username: email,
    Password: password,
  });

  return new Promise((resolve, reject) => {
    user.authenticateUser(details, {
      onSuccess: (session) => resolve(sessionToAuth(session)),
      onFailure: (err) => reject(err),
      newPasswordRequired: (userAttributes) => {
        if (!onNewPassword) {
          reject(new Error("NEW_PASSWORD_REQUIRED"));
          return;
        }
        // These attrs are not delivered on completion and must be dropped.
        delete userAttributes.email_verified;
        delete userAttributes.email;
        onNewPassword()
          .then((newPassword) =>
            user.completeNewPasswordChallenge(newPassword, userAttributes, {
              onSuccess: (session) => resolve(sessionToAuth(session)),
              onFailure: (err) => reject(err),
            }),
          )
          .catch(reject);
      },
    });
  });
}

/** Restore a persisted session (localStorage) on page load. */
export function currentSession(): Promise<AuthSession | null> {
  const user = userPool.getCurrentUser();
  if (!user) return Promise.resolve(null);
  return new Promise((resolve) => {
    user.getSession((err: Error | null, session: CognitoUserSession | null) => {
      if (err || !session || !session.isValid()) {
        resolve(null);
        return;
      }
      resolve(sessionToAuth(session));
    });
  });
}

export function signOut(): void {
  const user = userPool.getCurrentUser();
  if (user) user.signOut();
}
