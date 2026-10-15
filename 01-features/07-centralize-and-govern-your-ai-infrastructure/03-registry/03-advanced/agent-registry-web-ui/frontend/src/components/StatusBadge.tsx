// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import StatusIndicator, {
  type StatusIndicatorProps,
} from "@cloudscape-design/components/status-indicator";

const MAP: Record<string, StatusIndicatorProps.Type> = {
  APPROVED: "success",
  PENDING_APPROVAL: "pending",
  REJECTED: "error",
  DRAFT: "info",
  DEPRECATED: "stopped",
  CREATING: "in-progress",
  UPDATING: "in-progress",
  CREATE_FAILED: "error",
  UPDATE_FAILED: "error",
  READY: "success",
};

export default function StatusBadge({ status }: { status?: string }) {
  if (!status) return <>-</>;
  const type = MAP[status] ?? "info";
  return (
    <StatusIndicator type={type}>{status.replace(/_/g, " ")}</StatusIndicator>
  );
}
