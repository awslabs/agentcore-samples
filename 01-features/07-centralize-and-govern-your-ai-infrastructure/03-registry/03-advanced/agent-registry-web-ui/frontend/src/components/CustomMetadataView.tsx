// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import Box from "@cloudscape-design/components/box";
import KeyValuePairs from "@cloudscape-design/components/key-value-pairs";
import Link from "@cloudscape-design/components/link";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import {
  complianceLabel,
  displayValues,
  type MetadataField,
} from "./customMetadata";

interface Props {
  /** undefined on a Schema_Load_Failure: every value shows as raw text. */
  fields: MetadataField[] | undefined;
  values: Record<string, unknown> | undefined;
}

/**
 * Read-only list of a record's custom metadata (Req 8.1-8.4). Labels and
 * formatting come from `displayValues`; only `link` entries (http/https URLs)
 * render as links, everything else as plain text.
 */
export default function CustomMetadataView({ fields, values }: Props) {
  const items = displayValues(fields, values);
  if (items.length === 0) {
    return <Box color="text-status-inactive">No custom metadata.</Box>;
  }
  return (
    <KeyValuePairs
      columns={2}
      items={items.map((item) => ({
        label: item.label,
        value:
          item.kind === "link" ? (
            <Link
              external
              href={item.href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {item.href}
            </Link>
          ) : (
            item.text
          ),
      }))}
    />
  );
}

/** Req 8.5. "Compliant" / "Non-compliant" indicator, or "-" without a status. */
export function ComplianceBadge({ status }: { status?: string }) {
  const { type, label } = complianceLabel(status);
  if (type === "none") return <>{label}</>;
  return <StatusIndicator type={type}>{label}</StatusIndicator>;
}
