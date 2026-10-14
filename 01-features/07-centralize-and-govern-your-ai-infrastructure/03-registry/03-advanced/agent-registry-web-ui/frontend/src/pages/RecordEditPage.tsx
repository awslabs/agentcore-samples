// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Container from "@cloudscape-design/components/container";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Textarea from "@cloudscape-design/components/textarea";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import { useClients } from "../api/useClients";
import { useAuth } from "../auth/AuthContext";
import { getRecord, updateRecord } from "../api/records";
import { toFriendlyError, type FriendlyError } from "../api/errors";
import { useBreadcrumbs } from "../components/Breadcrumbs";
import { useRegistrySchema } from "../api/useRegistrySchema";
import CustomMetadataForm from "../components/CustomMetadataForm";
import {
  buildMetadataUpdate,
  parseSchema,
  pickFields,
  toCustomMetadata,
  validateMetadata,
  type CustomMetadata,
  type MetadataValue,
} from "../components/customMetadata";

/** Stored string/boolean values of a record's customMetadata document. */
function storedMetadata(doc: unknown): CustomMetadata | undefined {
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) return undefined;
  const out: CustomMetadata = {};
  for (const [k, v] of Object.entries(doc as Record<string, unknown>)) {
    if (typeof v === "string" || typeof v === "boolean") out[k] = v;
  }
  return out;
}

export default function RecordEditPage() {
  const { registryId = "", recordId = "" } = useParams();
  const navigate = useNavigate();
  const clients = useClients();
  const { capabilities } = useAuth();

  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [version, setVersion] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<FriendlyError | null>(null);
  // Custom metadata (Req 7). `originalMetadata` is the comparison baseline for
  // buildMetadataUpdate and is only set on load, so a failed save keeps it.
  const [recordType, setRecordType] = useState<string | null>(null);
  const [originalMetadata, setOriginalMetadata] = useState<
    CustomMetadata | undefined
  >(undefined);
  const [metadata, setMetadata] = useState<Record<string, MetadataValue>>({});
  const [metadataReady, setMetadataReady] = useState(false);
  const {
    config,
    loading: schemaLoading,
    loadError,
  } = useRegistrySchema(registryId);
  const parsed = useMemo(
    () => (recordType === null ? null : parseSchema(config, recordType)),
    [config, recordType],
  );
  // Schema_Load_Failure (loadError or a parse error) or no fields: hide the
  // form and omit customMetadata from the update (Req 7.8).
  const metadataFields = useMemo(
    () => (!loadError && parsed?.ok ? parsed.fields : []),
    [loadError, parsed],
  );

  const load = useCallback(async () => {
    if (!clients) return;
    setLoading(true);
    try {
      const r = await getRecord(clients, registryId, recordId);
      setName(r.name ?? "");
      setDisplayName(r.displayName ?? "");
      setDescription(r.description ?? "");
      setVersion(r.recordVersion ?? "");
      setOriginalMetadata(storedMetadata(r.customMetadata));
      setRecordType(r.recordType ?? "");
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setLoading(false);
    }
  }, [clients, registryId, recordId, capabilities?.persona]);

  useEffect(() => {
    void load();
  }, [load]);
  // Pre-fill once, after both the record and the schema have loaded, with the
  // stored values restricted to the record type's fields (Req 7.1).
  useEffect(() => {
    if (metadataReady || recordType === null || schemaLoading) return;
    setMetadata(pickFields(metadataFields, originalMetadata ?? {}));
    setMetadataReady(true);
  }, [metadataReady, recordType, schemaLoading, metadataFields, originalMetadata]);
  const showMetadata = metadataReady && metadataFields.length > 0;
  const metadataPayload = toCustomMetadata(metadataFields, metadata);
  const metadataErrors = showMetadata
    ? validateMetadata(metadataFields, metadataPayload)
    : [];

  useBreadcrumbs([
    { text: "Registries", href: "/" },
    { text: registryId, href: `/registries/${registryId}` },
    {
      text: name || recordId,
      href: `/registries/${registryId}/records/${recordId}`,
    },
    {
      text: "Edit",
      href: `/registries/${registryId}/records/${recordId}/edit`,
    },
  ]);

  const save = async () => {
    if (!clients) return;
    // The Save button is disabled while errors exist; Enter-to-submit is too.
    if (metadataErrors.length > 0) return;
    setSaving(true);
    setError(null);
    try {
      await updateRecord(clients, {
        registryId,
        recordId,
        name: name || undefined,
        displayName,
        description,
        recordVersion: version || undefined,
        ...(showMetadata
          ? {
              customMetadata: buildMetadataUpdate(
                originalMetadata,
                metadataPayload,
              ),
            }
          : {}),
      });
      navigate(`/registries/${registryId}/records/${recordId}`);
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
      setSaving(false);
    }
  };

  return (
    <ContentLayout header={<Header variant="h1">Edit record</Header>}>
      <Container>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
        >
          <Form
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button
                  variant="link"
                  onClick={() =>
                    navigate(`/registries/${registryId}/records/${recordId}`)
                  }
                >
                  Cancel
                </Button>
                <Button
                  variant="primary"
                  loading={saving}
                  disabled={loading || metadataErrors.length > 0}
                >
                  Save changes
                </Button>
              </SpaceBetween>
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
              <FormField label="Name">
                <Input
                  value={name}
                  onChange={({ detail }) => setName(detail.value)}
                />
              </FormField>
              <FormField label="Display name">
                <Input
                  value={displayName}
                  onChange={({ detail }) => setDisplayName(detail.value)}
                />
              </FormField>
              <FormField label="Description">
                <Textarea
                  value={description}
                  onChange={({ detail }) => setDescription(detail.value)}
                />
              </FormField>
              <FormField label="Version">
                <Input
                  value={version}
                  onChange={({ detail }) => setVersion(detail.value)}
                />
              </FormField>
              <Box>
                Descriptor content editing is done on create; this form updates
                metadata only.
              </Box>
              {showMetadata && (
                <SpaceBetween size="m">
                  <Header
                    variant="h2"
                    description="Attributes defined by this registry's custom metadata schema. If you set any value, every required field must be filled. Clearing every value removes the record's custom metadata."
                  >
                    Custom metadata
                  </Header>
                  <CustomMetadataForm
                    fields={metadataFields}
                    value={metadata}
                    onChange={setMetadata}
                    errors={metadataErrors}
                  />
                </SpaceBetween>
              )}
            </SpaceBetween>
          </Form>
        </form>
      </Container>
    </ContentLayout>
  );
}
