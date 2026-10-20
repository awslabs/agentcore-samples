// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
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

const schemaHeader = {
  headerText: "Custom metadata schema",
  headerDescription:
    "View field definitions. Edit the schema in the AWS Console or when provisioning this sample.",
  variant: "container" as const,
  defaultExpanded: false,
};

/**
 * Read-only view of the registry's custom metadata schema (Req 9). One tab per
 * record type, each listing that type's Effective_Field_Set. Offers no control
 * that changes the schema (Req 9.5): an Operator sets it outside the app.
 */
export default function CustomMetadataSchema({ config, loadError, loading }: Props) {
  if (loading) {
    return (
      <ExpandableSection {...schemaHeader}>
        <Spinner /> Loading schema…
      </ExpandableSection>
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
      <ExpandableSection {...schemaHeader}>
        <Box color="text-status-inactive">
          This registry defines no custom metadata schema. Edit the schema in
          the AWS Console or when provisioning this sample.
        </Box>
      </ExpandableSection>
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
    <ExpandableSection {...schemaHeader}>
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
    </ExpandableSection>
  );
}
