// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Container from "@cloudscape-design/components/container";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Spinner from "@cloudscape-design/components/spinner";
import CopyToClipboard from "@cloudscape-design/components/copy-to-clipboard";
import { useClients } from "../api/useClients";
import { useAuth } from "../auth/AuthContext";
import {
  getRecord,
  submitForApproval,
  setRecordStatus,
  listRecordTags,
} from "../api/records";
import { batchGetDiscoverable } from "../api/discovery";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import StatusBadge from "../components/StatusBadge";
import ReasonModal from "../components/ReasonModal";
import ConsumptionGuide from "../components/ConsumptionGuide";
import { summarizeDescriptorSource } from "../components/source";
import { useBreadcrumbs } from "../components/Breadcrumbs";

// Normalized view over both the control-plane record and the data-plane
// discoverable record (which a Consumer reads for approved records).
interface RecordView {
  name?: string;
  displayName?: string;
  description?: string;
  recordType?: string;
  status?: string;
  recordVersion?: string;
  statusReason?: string;
  recordArn?: string;
  descriptors?: any;
}

function descriptorJson(rec: RecordView): string {
  const d = rec.descriptors ?? {};
  const branch =
    d.mcpServer ?? d.a2aAgentCard ?? d.agentSkillsDefinition ?? d.custom;
  const data = (branch as { data?: string } | undefined)?.data;
  if (typeof data === "string") {
    try {
      return JSON.stringify(JSON.parse(data), null, 2);
    } catch {
      return data;
    }
  }
  return JSON.stringify(d, null, 2);
}

type ModalKind = null | "reject" | "deprecate";

/** Render a field value with an inline copy-to-clipboard icon (falls back to '-'). */
function copyable(value: string | undefined, label: string) {
  if (!value) return "-";
  return (
    <CopyToClipboard
      variant="inline"
      textToCopy={value}
      copyButtonAriaLabel={`Copy ${label}`}
      copySuccessText={`${label} copied`}
      copyErrorText={`Failed to copy ${label}`}
    />
  );
}

export default function RecordDetailPage() {
  const { registryId = "", recordId = "" } = useParams();
  const navigate = useNavigate();
  const clients = useClients();
  const { capabilities } = useAuth();

  const [rec, setRec] = useState<RecordView | null>(null);
  // Which plane the record came from. The data-plane (discovery) view strips the
  // source's outbound credential, so the Source panel must not claim there is none.
  const [readVia, setReadVia] = useState<"control" | "data">("control");
  const [tags, setTags] = useState<Record<string, string> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<FriendlyError | null>(null);
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<ModalKind>(null);

  const load = useCallback(async () => {
    if (!clients) return;
    setLoading(true);
    setError(null);
    // Personas that can only discover (Consumer) read approved records via the
    // data-plane batch-get; richer personas use the control-plane get. If the
    // control-plane call is denied, fall back to the data-plane view.
    const canControlRead = Boolean(
      capabilities && (capabilities.canPublish || capabilities.canApprove),
    );
    try {
      if (canControlRead) {
        setRec((await getRecord(clients, registryId, recordId)) as RecordView);
        setReadVia("control");
      } else {
        const [r] = await batchGetDiscoverable(clients, registryId, [recordId]);
        if (!r)
          throw {
            name: "ResourceNotFoundException",
            message: "Record not found or not approved.",
          };
        setRec(r as RecordView);
        setReadVia("data");
      }
    } catch (e) {
      const fe = toFriendlyError(e, capabilities?.persona);
      if (fe.isAccessDenied && canControlRead) {
        // Approved record a richer role couldn't control-read for some reason: try data-plane.
        try {
          const [r] = await batchGetDiscoverable(clients, registryId, [
            recordId,
          ]);
          if (r) {
            setRec(r as RecordView);
            setReadVia("data");
            setLoading(false);
            return;
          }
        } catch {
          /* fall through to the original error */
        }
      }
      setError(fe);
    } finally {
      setLoading(false);
    }
  }, [clients, registryId, recordId, capabilities]);

  useEffect(() => {
    void load();
  }, [load]);

  // Tags are NOT part of GetRegistryRecord — they are read separately against the
  // record ARN. Non-fatal: a role without ListTagsForResource simply sees no tags.
  useEffect(() => {
    if (!clients || !rec?.recordArn) {
      setTags(null);
      return;
    }
    let cancelled = false;
    listRecordTags(clients, rec.recordArn)
      .then((t) => {
        if (!cancelled) setTags(t);
      })
      .catch(() => {
        if (!cancelled) setTags(null);
      });
    return () => {
      cancelled = true;
    };
  }, [clients, rec?.recordArn]);

  useBreadcrumbs([
    { text: "Registries", href: "/" },
    { text: registryId, href: `/registries/${registryId}` },
    {
      text: rec?.displayName || rec?.name || recordId,
      href: `/registries/${registryId}/records/${recordId}`,
    },
  ]);

  const run = async (fn: () => Promise<void>) => {
    if (!clients) return;
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setBusy(false);
    }
  };

  const status = rec?.status;
  const caps = capabilities;

  return (
    <ContentLayout
      header={
        <Header
          variant="h1"
          actions={<Button iconName="refresh" onClick={() => void load()} />}
          description={`Record ${recordId} in registry ${registryId}`}
        >
          {rec?.displayName || rec?.name || "Record"}
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

        {loading ? (
          <Box textAlign="center" padding="xxl">
            <Spinner size="large" />
          </Box>
        ) : rec ? (
          <>
            <Container
              header={
                <Header
                  variant="h2"
                  actions={
                    <SpaceBetween direction="horizontal" size="xs">
                      {/* Publisher: submit a DRAFT for approval */}
                      {caps?.canPublish && status === "DRAFT" && (
                        <Button
                          loading={busy}
                          onClick={() =>
                            void run(() =>
                              submitForApproval(clients!, registryId, recordId),
                            )
                          }
                        >
                          Submit for approval
                        </Button>
                      )}
                      {/* Approver: approve or reject a PENDING record */}
                      {caps?.canApprove && status === "PENDING_APPROVAL" && (
                        <>
                          <Button
                            variant="primary"
                            loading={busy}
                            onClick={() =>
                              void run(() =>
                                setRecordStatus(
                                  clients!,
                                  registryId,
                                  recordId,
                                  "APPROVED",
                                  "Approved via console",
                                ),
                              )
                            }
                          >
                            Approve
                          </Button>
                          <Button onClick={() => setModal("reject")}>
                            Reject
                          </Button>
                        </>
                      )}
                      {/* Approver: deprecate an APPROVED record (the terminal state) */}
                      {caps?.canApprove && status === "APPROVED" && (
                        <Button onClick={() => setModal("deprecate")}>
                          Deprecate
                        </Button>
                      )}
                      {/* Publisher: edit metadata */}
                      {caps?.canPublish && (
                        <Button
                          onClick={() =>
                            navigate(
                              `/registries/${registryId}/records/${recordId}/edit`,
                            )
                          }
                        >
                          Edit
                        </Button>
                      )}
                    </SpaceBetween>
                  }
                >
                  Details
                </Header>
              }
            >
              <KeyValuePairs
                columns={2}
                items={[
                  { label: "Name", value: copyable(rec.name, "name") },
                  {
                    label: "Display name",
                    value: copyable(rec.displayName, "display name"),
                  },
                  { label: "Type", value: rec.recordType ?? "-" },
                  {
                    label: "Status",
                    value: <StatusBadge status={rec.status} />,
                  },
                  { label: "Version", value: rec.recordVersion ?? "-" },
                  { label: "Description", value: rec.description ?? "-" },
                  { label: "Status reason", value: rec.statusReason ?? "-" },
                  {
                    label: "Record ARN",
                    value: copyable(rec.recordArn, "record ARN"),
                  },
                ]}
              />
            </Container>

            {(() => {
              const src = summarizeDescriptorSource(rec.descriptors, readVia);
              if (!src) return null;
              const label =
                src.credentialType === "OAUTH"
                  ? "OAuth 2.0 · AgentCore Identity"
                  : src.credentialType === "IAM"
                    ? "IAM SigV4"
                    : src.credentialType === "NONE"
                      ? "None (public endpoint)"
                      : "Not shown in the discovery view";
              return (
                <Container
                  header={
                    <Header
                      variant="h2"
                      description="This descriptor is synchronized: the registry calls the endpoint below and populates the descriptor from what it finds."
                    >
                      Source &amp; authentication
                    </Header>
                  }
                >
                  <KeyValuePairs
                    columns={2}
                    items={[
                      {
                        label: "Source URL",
                        value: copyable(src.url, "source URL"),
                      },
                      { label: "Outbound authentication", value: label },
                      {
                        label:
                          src.credentialType === "IAM"
                            ? "Role ARN"
                            : "Credential provider",
                        value: src.credentialRef
                          ? copyable(src.credentialRef, "credential reference")
                          : "-",
                      },
                      { label: "Details", value: src.detail || "-" },
                    ]}
                  />
                </Container>
              );
            })()}

            {tags && Object.keys(tags).length > 0 && (
              <Container
                header={
                  <Header
                    variant="h2"
                    description="Applied at creation, or afterwards with TagResource. Tags are metadata — they are not a discovery search filter."
                  >
                    Tags
                  </Header>
                }
              >
                <KeyValuePairs
                  columns={2}
                  items={Object.keys(tags)
                    .sort()
                    .map((k) => ({ label: k, value: tags[k] || "-" }))}
                />
              </Container>
            )}

            <ConsumptionGuide rec={rec} />

            <Container header={<Header variant="h2">Descriptor</Header>}>
              <Box variant="code">
                <pre style={{ whiteSpace: "pre-wrap", margin: 0 }}>
                  {descriptorJson(rec)}
                </pre>
              </Box>
            </Container>
          </>
        ) : null}
      </SpaceBetween>

      <ReasonModal
        visible={modal === "reject"}
        title="Reject record"
        actionLabel="Reject"
        onDismiss={() => setModal(null)}
        onConfirm={async (reason) => {
          setModal(null);
          await run(() =>
            setRecordStatus(clients!, registryId, recordId, "REJECTED", reason),
          );
        }}
      />
      <ReasonModal
        visible={modal === "deprecate"}
        title="Deprecate record"
        actionLabel="Deprecate"
        onDismiss={() => setModal(null)}
        onConfirm={async (reason) => {
          setModal(null);
          await run(() =>
            setRecordStatus(
              clients!,
              registryId,
              recordId,
              "DEPRECATED",
              reason,
            ),
          );
        }}
      />
    </ContentLayout>
  );
}
