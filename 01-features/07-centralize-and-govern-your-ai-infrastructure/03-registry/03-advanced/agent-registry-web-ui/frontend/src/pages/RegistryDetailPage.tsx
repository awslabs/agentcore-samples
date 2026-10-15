// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Table from "@cloudscape-design/components/table";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TextFilter from "@cloudscape-design/components/text-filter";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import Alert from "@cloudscape-design/components/alert";
import Link from "@cloudscape-design/components/link";
import { useClients } from "../api/useClients";
import { useAuth } from "../auth/AuthContext";
import { listRecords, type RegistryRecordSummary } from "../api/records";
import { getRegistry, type GetRegistryResponse } from "../api/registry";
import {
  searchRecords,
  listDiscoverable,
  type DiscoverableRecord,
} from "../api/discovery";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import StatusBadge from "../components/StatusBadge";
import { useBreadcrumbs } from "../components/Breadcrumbs";
import ConnectRegistry from "../components/ConnectRegistry";

const RECORD_TYPES = [
  { label: "All types", value: "" },
  { label: "MCP", value: "MCP" },
  { label: "Agent", value: "AGENT" },
  { label: "Skill", value: "SKILL" },
  { label: "Custom", value: "CUSTOM" },
];

const STATUS_OPTIONS = [
  { label: "All statuses", value: "" },
  { label: "Draft", value: "DRAFT" },
  { label: "Pending approval", value: "PENDING_APPROVAL" },
  { label: "Approved", value: "APPROVED" },
  { label: "Rejected", value: "REJECTED" },
  { label: "Deprecated", value: "DEPRECATED" },
];

export default function RegistryDetailPage() {
  const { registryId = "" } = useParams();
  const navigate = useNavigate();
  const clients = useClients();
  const { capabilities } = useAuth();

  // Consumers can't list all (control-plane) records — land them on Discover.
  const canListAll = Boolean(
    capabilities && (capabilities.canPublish || capabilities.canApprove),
  );
  const [mode, setMode] = useState(canListAll ? "records" : "discover");
  const [registry, setRegistry] = useState<GetRegistryResponse | null>(null);
  const [records, setRecords] = useState<RegistryRecordSummary[]>([]);
  const [filterText, setFilterText] = useState("");
  const [recordsType, setRecordsType] = useState(RECORD_TYPES[0]);
  const [recordsStatus, setRecordsStatus] = useState(STATUS_OPTIONS[0]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<FriendlyError | null>(null);

  // Discover (data-plane) state
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState(RECORD_TYPES[0]);
  const [results, setResults] = useState<DiscoverableRecord[]>([]);
  const [searching, setSearching] = useState(false);

  const loadRecords = useCallback(async () => {
    if (!clients) return;
    setLoading(true);
    setError(null);
    try {
      setRecords(await listRecords(clients, registryId));
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setLoading(false);
    }
  }, [clients, registryId, capabilities?.persona]);

  useEffect(() => {
    if (canListAll) void loadRecords();
    else setLoading(false);
  }, [loadRecords, canListAll]);

  // The registry's authorizerType drives the connect instructions. Every persona can
  // read it (GetRegistry is allowed for all three roles); a failure here is not fatal
  // to the page, so it does not raise the page-level error banner.
  useEffect(() => {
    if (!clients) return;
    let cancelled = false;
    getRegistry(clients, registryId)
      .then((r) => {
        if (!cancelled) setRegistry(r);
      })
      .catch(() => {
        /* non-fatal: the connect tab falls back to the configured auth mode */
      });
    return () => {
      cancelled = true;
    };
  }, [clients, registryId]);

  useBreadcrumbs([
    { text: "Registries", href: "/" },
    { text: registryId, href: `/registries/${registryId}` },
  ]);

  const runSearch = useCallback(async () => {
    if (!clients) return;
    setSearching(true);
    setError(null);
    const type = typeFilter.value || undefined;
    try {
      if (query.trim()) {
        // NL search, narrowed by the selected record type (metadata filter).
        setResults(
          await searchRecords(clients, registryId, query.trim(), type),
        );
      } else {
        setResults(await listDiscoverable(clients, registryId, type));
      }
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setSearching(false);
    }
  }, [clients, registryId, query, typeFilter.value, capabilities?.persona]);

  // Reactively re-run discovery when the tab, query, or type filter changes.
  // Debounced so typing in the search box doesn't fire a request per keystroke.
  useEffect(() => {
    if (mode !== "discover") return;
    const t = setTimeout(() => void runSearch(), 300);
    return () => clearTimeout(t);
  }, [mode, query, typeFilter.value, runSearch]);

  const filtered = records.filter((r) => {
    const matchesText = [r.name, r.recordType, r.status].some((v) =>
      (v ?? "").toLowerCase().includes(filterText.toLowerCase()),
    );
    const matchesType =
      !recordsType.value || r.recordType === recordsType.value;
    const matchesStatus =
      !recordsStatus.value || r.status === recordsStatus.value;
    return matchesText && matchesType && matchesStatus;
  });

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={() => void loadRecords()} />
              {capabilities?.canPublish && (
                <Button
                  variant="primary"
                  onClick={() =>
                    navigate(`/registries/${registryId}/records/new`)
                  }
                >
                  Create record
                </Button>
              )}
            </SpaceBetween>
          }
          description={`Registry ${registryId}`}
        >
          Records
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

        <SegmentedControl
          selectedId={mode}
          onChange={({ detail }) => setMode(detail.selectedId)}
          options={[
            ...(canListAll
              ? [{ id: "records", text: "Manage (all records)" }]
              : []),
            { id: "discover", text: "Discover (search approved)" },
            { id: "connect", text: "Connect your tools" },
          ]}
        />
        <Box color="text-body-secondary" fontSize="body-s">
          {mode === "records"
            ? "All records in this registry across every status (control-plane). Authoring and governance live here."
            : mode === "discover"
              ? "The consumer discovery experience: natural-language search over APPROVED records only (data-plane) — the same path an agent uses at runtime."
              : "Wire this registry into an MCP client (Kiro, Amazon Quick, Claude) so an agent discovers its approved records at runtime."}
        </Box>

        {mode === "connect" ? (
          <ConnectRegistry
            registryId={registryId}
            authorizerType={registry?.discoveryConfiguration?.authorizerType}
          />
        ) : mode === "records" ? (
          <Table<RegistryRecordSummary>
            variant="container"
            loading={loading}
            loadingText="Loading records"
            items={filtered}
            trackBy="recordId"
            filter={
              <SpaceBetween direction="horizontal" size="xs">
                <div style={{ minWidth: 260 }}>
                  <TextFilter
                    filteringText={filterText}
                    filteringPlaceholder="Find records"
                    onChange={({ detail }) =>
                      setFilterText(detail.filteringText)
                    }
                  />
                </div>
                <Select
                  selectedOption={recordsType}
                  onChange={({ detail }) =>
                    setRecordsType(detail.selectedOption as typeof recordsType)
                  }
                  options={RECORD_TYPES}
                />
                <Select
                  selectedOption={recordsStatus}
                  onChange={({ detail }) =>
                    setRecordsStatus(
                      detail.selectedOption as typeof recordsStatus,
                    )
                  }
                  options={STATUS_OPTIONS}
                />
              </SpaceBetween>
            }
            columnDefinitions={[
              {
                id: "name",
                header: "Name",
                cell: (r) => (
                  <Link
                    onFollow={(e) => {
                      e.preventDefault();
                      navigate(
                        `/registries/${registryId}/records/${r.recordId}`,
                      );
                    }}
                  >
                    {r.displayName || r.name}
                  </Link>
                ),
                sortingField: "name",
              },
              { id: "type", header: "Type", cell: (r) => r.recordType ?? "-" },
              {
                id: "status",
                header: "Status",
                cell: (r) => <StatusBadge status={r.status} />,
              },
              {
                id: "version",
                header: "Version",
                cell: (r) => r.recordVersion ?? "-",
              },
            ]}
            empty={
              <Box textAlign="center" padding="l">
                <b>No records</b>
              </Box>
            }
          />
        ) : (
          <SpaceBetween size="l">
            <SpaceBetween direction="horizontal" size="xs">
              <div style={{ minWidth: 360 }}>
                <Input
                  value={query}
                  onChange={({ detail }) => setQuery(detail.value)}
                  placeholder="Natural-language search, e.g. 'restart unhealthy tasks'"
                  type="search"
                />
              </div>
              <Select
                selectedOption={typeFilter}
                onChange={({ detail }) =>
                  setTypeFilter(detail.selectedOption as typeof typeFilter)
                }
                options={RECORD_TYPES}
              />
              <Button
                iconName="refresh"
                loading={searching}
                onClick={() => void runSearch()}
                ariaLabel="Refresh results"
              />
            </SpaceBetween>
            <Table<DiscoverableRecord>
              variant="container"
              items={results}
              trackBy="recordId"
              columnDefinitions={[
                {
                  id: "name",
                  header: "Name",
                  cell: (r) => (
                    <Link
                      onFollow={(e) => {
                        e.preventDefault();
                        navigate(
                          `/registries/${registryId}/records/${r.recordId}`,
                        );
                      }}
                    >
                      {r.displayName || r.name}
                    </Link>
                  ),
                },
                {
                  id: "type",
                  header: "Type",
                  cell: (r) => r.recordType ?? "-",
                },
                {
                  id: "desc",
                  header: "Description",
                  cell: (r) => r.description ?? "-",
                },
              ]}
              empty={
                <Box textAlign="center" padding="l">
                  Run a search to discover approved records.
                </Box>
              }
            />
          </SpaceBetween>
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}
