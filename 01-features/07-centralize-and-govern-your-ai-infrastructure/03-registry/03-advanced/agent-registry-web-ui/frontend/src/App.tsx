// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Navigate, Route, Routes } from "react-router-dom";
import Spinner from "@cloudscape-design/components/spinner";
import Box from "@cloudscape-design/components/box";
import { useAuth } from "./auth/AuthContext";
import AppShell from "./components/AppShell";
import LoginPage from "./pages/LoginPage";
import RegistriesPage from "./pages/RegistriesPage";
import RegistryDetailPage from "./pages/RegistryDetailPage";
import RecordDetailPage from "./pages/RecordDetailPage";
import RecordCreatePage from "./pages/RecordCreatePage";
import RecordEditPage from "./pages/RecordEditPage";
import { type ReactNode } from "react";

function RequireAuth({ children }: { children: ReactNode }) {
  const { session, loading } = useAuth();
  if (loading) {
    return (
      <Box textAlign="center" padding="xxxl">
        <Spinner size="large" />
      </Box>
    );
  }
  if (!session) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const { session } = useAuth();
  return (
    <Routes>
      <Route
        path="/login"
        element={session ? <Navigate to="/" replace /> : <LoginPage />}
      />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <AppShell>
              <Routes>
                <Route path="/" element={<RegistriesPage />} />
                <Route
                  path="/registries/:registryId"
                  element={<RegistryDetailPage />}
                />
                <Route
                  path="/registries/:registryId/records/new"
                  element={<RecordCreatePage />}
                />
                <Route
                  path="/registries/:registryId/records/:recordId/edit"
                  element={<RecordEditPage />}
                />
                <Route
                  path="/registries/:registryId/records/:recordId"
                  element={<RecordDetailPage />}
                />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </AppShell>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
