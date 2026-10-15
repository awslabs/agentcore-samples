// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import {
  signIn as cognitoSignIn,
  signOut as cognitoSignOut,
  currentSession,
  type AuthSession,
} from "./cognito";
import { credentialProvider } from "./credentials";
import { capabilitiesFor, type Capabilities } from "../personas/capabilities";

interface AuthContextValue {
  session: AuthSession | null;
  capabilities: Capabilities | null;
  credentials: AwsCredentialIdentityProvider | null;
  loading: boolean;
  signIn: (
    email: string,
    password: string,
    onNewPassword?: () => Promise<string>,
  ) => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    currentSession()
      .then(setSession)
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(
    async (
      email: string,
      password: string,
      onNewPassword?: () => Promise<string>,
    ) => {
      const s = await cognitoSignIn(email, password, onNewPassword);
      setSession(s);
    },
    [],
  );

  const signOut = useCallback(() => {
    cognitoSignOut();
    setSession(null);
  }, []);

  const capabilities = useMemo(
    () => (session ? capabilitiesFor(session.groups) : null),
    [session],
  );

  // One credential provider per session; SDK clients memoize + refresh it.
  const credentials = useMemo(
    () => (session ? credentialProvider(session.idToken) : null),
    [session],
  );

  const value: AuthContextValue = {
    session,
    capabilities,
    credentials,
    loading,
    signIn,
    signOut,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
