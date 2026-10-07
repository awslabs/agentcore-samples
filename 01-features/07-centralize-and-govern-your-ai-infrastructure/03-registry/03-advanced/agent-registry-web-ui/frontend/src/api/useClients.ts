// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useMemo } from "react";
import { useAuth } from "../auth/AuthContext";
import { makeClients, type Clients } from "./client";

/**
 * Returns the API surface bound to the current session: persona-scoped IAM
 * credentials for the control plane, plus the Cognito access token used as the
 * bearer credential for a CUSTOM_JWT registry's discovery endpoint.
 */
export function useClients(): Clients | null {
  const { credentials, session } = useAuth();
  return useMemo(
    () =>
      credentials && session
        ? makeClients({ credentials, accessToken: session.accessToken })
        : null,
    [credentials, session],
  );
}
