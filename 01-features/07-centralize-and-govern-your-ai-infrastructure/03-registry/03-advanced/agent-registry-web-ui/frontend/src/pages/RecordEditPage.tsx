// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useCallback, useEffect, useState } from "react";
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

  const load = useCallback(async () => {
    if (!clients) return;
    setLoading(true);
    try {
      const r = await getRecord(clients, registryId, recordId);
      setName(r.name ?? "");
      setDisplayName(r.displayName ?? "");
      setDescription(r.description ?? "");
      setVersion(r.recordVersion ?? "");
    } catch (e) {
      setError(toFriendlyError(e, capabilities?.persona));
    } finally {
      setLoading(false);
    }
  }, [clients, registryId, recordId, capabilities?.persona]);

  useEffect(() => {
    void load();
  }, [load]);

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
                <Button variant="primary" loading={saving} disabled={loading}>
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
            </SpaceBetween>
          </Form>
        </form>
      </Container>
    </ContentLayout>
  );
}
