// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
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
import { useRegistrySchema } from "../api/useRegistrySchema";
import {
  searchRecords,
  listDiscoverable,
  type DiscoverableRecord,
} from "../api/discovery";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import StatusBadge from "../components/StatusBadge";
import { useBreadcrumbs } from "../components/Breadcrumbs";
import ConnectRegistry from "../components/ConnectRegistry";
import CustomMetadataSchema from "../components/CustomMetadataSchema";
import CustomMetadataFilters from "../components/CustomMetadataFilters";
import { ComplianceBadge } from "../components/CustomMetadataView";
import {
  buildSearchFilter,
  displayValues,
  exampleFilter,
  filterableFields,
  parseSchema,
  schemaRecordTypes,
  type FilterSelections,
  type MetadataField,
} from "../components/customMetadata";

/** True when any record type's schema fails to parse (part of Schema_Load_Failure). */
function schemaParseFails(
  config: CustomMetadataSchemaConfiguration | undefined,
): boolean {
  if (!config) return false;
  return schemaRecordTypes(config).some((t) => !parseSchema(config, t).ok);
}

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

const DISCOVERY_DEBOUNCE_MS = 300;

type DiscoverySnapshot = {
  requestId: number;
  query: string;
  type: string | undefined;
  metaFields: MetadataField[];
  metaFilters: FilterSelections;
};

/** An empty type selection permits every row; otherwise require an exact type. */
export function matchesSelectedType(
  record: { recordType?: string },
  selectedType: string | undefined,
): boolean {
  return !selectedType || record.recordType === selectedType;
}

/** Apply the selected-type invariant at every rendered-result commit boundary. */
export function filterResultsBySelectedType<T extends { recordType?: string }>(
  records: T[],
  selectedType: string | undefined,
): T[] {
  return records.filter((record) => matchesSelectedType(record, selectedType));
}

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
  // GetRegistry (allowed for every persona) supplies authorizerType for the
  // connect instructions and the custom metadata schema. A failure is not fatal
  // to the page: it only shows as a schema load error (Req 9.4).
  const {
    registry,
    config,
    loading: schemaLoading,
    loadError,
  } = useRegistrySchema(registryId);
  const schemaFailed = loadError || schemaParseFails(config);
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
  const latestDiscoveryRequestRef = useRef(0);
  const discoveryTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const invalidateDiscoveryRequests = useCallback(() => {
    latestDiscoveryRequestRef.current += 1;
    if (discoveryTimerRef.current) {
      clearTimeout(discoveryTimerRef.current);
      discoveryTimerRef.current = undefined;
    }
  }, []);
  // Custom metadata filter selections; cleared whenever the record type changes.
  const [metaFilters, setMetaFilters] = useState<FilterSelections>({});
  const searchMode = query.trim() !== "";
  const metaFields = useMemo(
    () => (schemaFailed ? [] : filterableFields(config, typeFilter.value)),
    [schemaFailed, config, typeFilter.value],
  );

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

  useBreadcrumbs([
    { text: "Registries", href: "/" },
    { text: registryId, href: `/registries/${registryId}` },
  ]);

  const createDiscoverySnapshot = useCallback((): DiscoverySnapshot => {
    const requestId = latestDiscoveryRequestRef.current + 1;
    latestDiscoveryRequestRef.current = requestId;
    return {
      requestId,
      query: query.trim(),
      type: typeFilter.value || undefined,
      metaFields: [...metaFields],
      metaFilters: { ...metaFilters },
    };
  }, [query, typeFilter.value, metaFields, metaFilters]);

  const runSearch = useCallback(
    async (snapshot: DiscoverySnapshot) => {
      if (!clients) return;
      const isCurrent = () =>
        snapshot.requestId === latestDiscoveryRequestRef.current;

      if (!isCurrent()) return;
      setSearching(true);
      setError(null);
      try {
        const discovered = snapshot.query
          ? await searchRecords(
              clients,
              registryId,
              snapshot.query,
              buildSearchFilter(
                snapshot.type ?? "",
                snapshot.metaFields,
                snapshot.metaFilters,
              ),
            )
          : await listDiscoverable(
              clients,
              registryId,
              snapshot.type,
              true,
            );
        const typeMatched = filterResultsBySelectedType(discovered, snapshot.type);
        if (isCurrent()) setResults(typeMatched);
      } catch (e) {
        // Selections stay as they are so the user can adjust and retry (Req 12.3).
        if (isCurrent()) {
          setError(toFriendlyError(e, capabilities?.persona));
        }
      } finally {
        if (isCurrent()) setSearching(false);
      }
    },
    [clients, registryId, capabilities?.persona],
  );

  const refreshSearch = useCallback(() => {
    if (discoveryTimerRef.current) {
      clearTimeout(discoveryTimerRef.current);
      discoveryTimerRef.current = undefined;
    }
    void runSearch(createDiscoverySnapshot());
  }, [createDiscoverySnapshot, runSearch]);

  /** Req 8.7. Use bold keys and normal values for scannable table metadata. */
  const metadataSummary = (r: DiscoverableRecord) => {
    let fields: MetadataField[] | undefined;
    if (!schemaFailed) {
      const parsed = parseSchema(config, r.recordType ?? "");
      fields = parsed.ok ? parsed.fields : undefined;
    }
    const items = displayValues(fields, r.customMetadata);
    if (items.length === 0) return "-";
    return (
      <SpaceBetween size="xxs">
        {items.map((item, index) => (
          <Box key={`${item.label}-${index}`}>
            <Box variant="span" fontWeight="bold">
              {item.label}:&nbsp;
            </Box>
            <Box variant="span" fontWeight="normal">
              {item.kind === "link" ? item.href : item.text}
            </Box>
          </Box>
        ))}
      </SpaceBetween>
    );
  };

  // Reactively re-run discovery when the tab or discovery inputs change.
  // A snapshot prevents an older completion from updating a newer search.
  useEffect(() => {
    if (mode !== "discover") {
      invalidateDiscoveryRequests();
      return;
    }

    const snapshot = createDiscoverySnapshot();
    setResults([]);
    setError(null);
    setSearching(false);
    discoveryTimerRef.current = setTimeout(() => {
      discoveryTimerRef.current = undefined;
      void runSearch(snapshot);
    }, DISCOVERY_DEBOUNCE_MS);

    return () => {
      if (discoveryTimerRef.current) {
        clearTimeout(discoveryTimerRef.current);
        discoveryTimerRef.current = undefined;
      }
    };
  }, [
    mode,
    query,
    typeFilter.value,
    metaFilters,
    createDiscoverySnapshot,
    invalidateDiscoveryRequests,
    runSearch,
  ]);

  useEffect(
    () => () => {
      latestDiscoveryRequestRef.current += 1;
      if (discoveryTimerRef.current) clearTimeout(discoveryTimerRef.current);
    },
    [],
  );

  const filtered = records.filter((r) => {
    const matchesText = [r.name, r.recordType, r.status].some((v) =>
      (v ?? "").toLowerCase().includes(filterText.toLowerCase()),
    );
    const matchesType = matchesSelectedType(r, recordsType.value);
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
          onChange={({ detail }) => {
            invalidateDiscoveryRequests();
            setMode(detail.selectedId);
          }}
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
            exampleFilter={schemaFailed ? undefined : exampleFilter(config)}
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
              {
                id: "compliance",
                header: "Compliance",
                cell: (r) => (
                  <ComplianceBadge
                    status={r.customMetadataSchemaComplianceStatus}
                  />
                ),
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
                  onChange={({ detail }) => {
                    invalidateDiscoveryRequests();
                    setQuery(detail.value);
                  }}
                  placeholder="Natural-language search, e.g. 'restart unhealthy tasks'"
                  type="search"
                />
              </div>
              <Select
                selectedOption={typeFilter}
                onChange={({ detail }) => {
                  invalidateDiscoveryRequests();
                  setTypeFilter(detail.selectedOption as typeof typeFilter);
                  setMetaFilters({}); // Req 10.3
                }}
                options={RECORD_TYPES}
              />
              <Button
                iconName="refresh"
                loading={searching}
                onClick={refreshSearch}
                ariaLabel="Refresh results"
              />
            </SpaceBetween>
            {searchMode && metaFields.length > 0 && (
              <CustomMetadataFilters
                fields={metaFields}
                value={metaFilters}
                onChange={(nextFilters) => {
                  invalidateDiscoveryRequests();
                  setMetaFilters(nextFilters);
                }}
              />
            )}
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
                {
                  id: "customMetadata",
                  header: "Custom metadata",
                  cell: metadataSummary,
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

        <CustomMetadataSchema
          config={config}
          loadError={loadError}
          loading={schemaLoading}
        />
      </SpaceBetween>
    </ContentLayout>
  );
}
