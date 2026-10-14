// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import Spinner from "@cloudscape-design/components/spinner";
import Table from "@cloudscape-design/components/table";
import Tabs from "@cloudscape-design/components/tabs";
import {
  parseSchema,
  schemaRecordTypes,
  type FieldType,
  type MetadataField,
} from "./customMetadata";

interface Props {
  config: CustomMetadataSchemaConfiguration | undefined;
  /** GetRegistry failed (Schema_Load_Failure). */
  loadError?: boolean;
  loading?: boolean;
}

const TYPE_LABELS: Record<FieldType, string> = {
  text: "Text",
  enum: "Enum",
  url: "URL",
  boolean: "Boolean",
};

const HEADER = (
  <Header
    variant="h2"
    description="Fields a record can carry, per record type. Read-only."
  >
    Custom metadata schema
  </Header>
);

/**
 * Read-only view of the registry's custom metadata schema (Req 9). One tab per
 * record type, each listing that type's Effective_Field_Set. Offers no control
 * that changes the schema (Req 9.5): an Operator sets it outside the app.
 */
export default function CustomMetadataSchema({ config, loadError, loading }: Props) {
  if (loading) {
    return (
      <Container header={HEADER}>
        <Spinner /> Loading schema…
      </Container>
    );
  }

  if (loadError) {
    return (
      <Alert type="error" header="Custom metadata schema could not be loaded">
        The registry's custom metadata schema could not be read. The rest of the
        page is still available.
      </Alert>
    );
  }

  const hasSchema =
    config !== undefined &&
    (config.defaultSchema !== undefined ||
      (config.recordTypeSchemaOverrides?.length ?? 0) > 0);
  if (!hasSchema) {
    return (
      <Container header={HEADER}>
        <Box color="text-status-inactive">
          This registry defines no custom metadata schema. An Operator sets the
          schema outside the app, when the registry is created.
        </Box>
      </Container>
    );
  }

  const types = schemaRecordTypes(config);
  const parsed: { type: string; fields: MetadataField[] }[] = [];
  for (const type of types) {
    const result = parseSchema(config, type);
    if (!result.ok) {
      return (
        <Alert type="error" header="Custom metadata schema could not be loaded">
          {result.error}
        </Alert>
      );
    }
    parsed.push({ type, fields: result.fields });
  }

  return (
    <Container header={HEADER}>
      <Tabs
        tabs={parsed.map(({ type, fields }) => ({
          id: type,
          label: type,
          content: (
            <Table
              variant="embedded"
              items={fields}
              trackBy="name"
              empty={
                <Box color="text-status-inactive">
                  No custom metadata fields for this record type.
                </Box>
              }
              columnDefinitions={[
                { id: "name", header: "Field", cell: (f) => f.name },
                { id: "type", header: "Type", cell: (f) => TYPE_LABELS[f.type] },
                {
                  id: "required",
                  header: "Required",
                  cell: (f) => (f.required ? "Yes" : "No"),
                },
                {
                  id: "values",
                  header: "Allowed values",
                  cell: (f) =>
                    f.type === "enum" && f.enumValues?.length
                      ? f.enumValues.join(", ")
                      : "-",
                },
                {
                  id: "source",
                  header: "Scope",
                  cell: (f) => (f.source === "common" ? "Common" : "Record type"),
                },
              ]}
            />
          ),
        }))}
      />
    </Container>
  );
}
