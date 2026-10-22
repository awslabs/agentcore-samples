// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Cards from "@cloudscape-design/components/cards";
import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Alert from "@cloudscape-design/components/alert";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Link from "@cloudscape-design/components/link";
import { useNavigate } from "react-router-dom";
import { useClients } from "../api/useClients";
import { useAuth } from "../auth/AuthContext";
import { listRegistries, type RegistrySummary } from "../api/registry";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import StatusBadge from "../components/StatusBadge";
import { useBreadcrumbs } from "../components/Breadcrumbs";

export default function RegistriesPage() {
  const clients = useClients();
  const { capabilities } = useAuth();
  const navigate = useNavigate();
  const [items, setItems] = useState<RegistrySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<FriendlyError | null>(null);

  const load = useCallback(async () => {
    if (!clients) return;
    setLoading(true);
    setError(null);
    try {
      setItems(await listRegistries(clients));
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setLoading(false);
    }
  }, [clients, capabilities?.persona]);

  useEffect(() => {
    void load();
  }, [load]);

  useBreadcrumbs([{ text: "Registries", href: "/" }]);

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={() => void load()} />
            </SpaceBetween>
          }
        >
          Registries
        </Header>
      }
    >
      <SpaceBetween size="l">
        {error && (
          <Alert
            type={error.isAccessDenied ? "warning" : "error"}
            header={error.title}
          >
            {error.message}
          </Alert>
        )}
        <Cards
          loading={loading}
          loadingText="Loading registries"
          items={items}
          trackBy="registryId"
          cardDefinition={{
            header: (r) => (
              <Link
                fontSize="heading-m"
                onFollow={(e) => {
                  e.preventDefault();
                  navigate(`/registries/${r.registryId}`);
                }}
              >
                {r.name}
              </Link>
            ),
            sections: [
              {
                id: "desc",
                content: (r) => r.description || <i>No description</i>,
              },
              {
                id: "status",
                header: "Status",
                content: (r) => <StatusBadge status={r.status} />,
              },
              { id: "id", header: "Registry ID", content: (r) => r.registryId },
              {
                id: "auth",
                header: "Authorizer",
                content: (r) => r.discoveryConfiguration?.authorizerType ?? "-",
              },
            ],
          }}
          empty={
            <Box textAlign="center" color="inherit" padding="l">
              <b>No registries</b>
              <Box variant="p" color="inherit">
                No registries are visible to your role yet. Registries are
                provisioned by an administrator in the console, the CLI or IaC —
                this UI does not create them.
              </Box>
            </Box>
          }
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
