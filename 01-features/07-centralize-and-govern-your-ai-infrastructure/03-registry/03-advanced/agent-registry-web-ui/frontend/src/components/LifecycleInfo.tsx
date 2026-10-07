// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Link from "@cloudscape-design/components/link";
import StatusBadge from "./StatusBadge";

const LIFECYCLE_DOC =
  "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-record-lifecycle.html";

const STEPS: { status: string; when: string }[] = [
  {
    status: "DRAFT",
    when: "On create. Edit freely before submitting; stays DRAFT.",
  },
  {
    status: "PENDING_APPROVAL",
    when: "After Submit for approval. Auto-approval (if the registry enables it) skips straight to APPROVED.",
  },
  {
    status: "APPROVED",
    when: "An approver approves it. Only APPROVED revisions are discoverable (search / list / MCP endpoint).",
  },
];

const BRANCHES: { status: string; note: string }[] = [
  {
    status: "REJECTED",
    note: "An approver rejects a pending record. Edit it to a new DRAFT and resubmit, or an approver can approve it directly. (Rejecting an approved record is also how you temporarily hide it from discovery.)",
  },
  {
    status: "DEPRECATED",
    note: "Terminal — reachable from any status and cannot be undone or edited. Use it to retire a record.",
  },
];

/** Informational card describing the record approval lifecycle (per AWS docs). */
export default function LifecycleInfo() {
  return (
    <Container header={<Header variant="h2">Record lifecycle</Header>}>
      <SpaceBetween size="m">
        <Box variant="p" color="text-body-secondary">
          A new record starts in <b>DRAFT</b>. When you submit it, an approver
          reviews it before it becomes discoverable. Only <b>APPROVED</b>{" "}
          records are returned by search, list, and the MCP endpoint.
        </Box>

        {/* Happy-path flow */}
        <Box>
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            {STEPS.map((s, i) => (
              <SpaceBetween
                key={s.status}
                direction="horizontal"
                size="xs"
                alignItems="center"
              >
                <StatusBadge status={s.status} />
                {i < STEPS.length - 1 && (
                  <Box color="text-body-secondary">→</Box>
                )}
              </SpaceBetween>
            ))}
          </SpaceBetween>
        </Box>

        {STEPS.map((s) => (
          <Box key={s.status}>
            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
              <StatusBadge status={s.status} />
              <Box variant="span" color="text-body-secondary" fontSize="body-s">
                {s.when}
              </Box>
            </SpaceBetween>
          </Box>
        ))}

        <ExpandableSection headerText="Other states & rules">
          <SpaceBetween size="s">
            {BRANCHES.map((b) => (
              <Box key={b.status}>
                <SpaceBetween
                  direction="horizontal"
                  size="xs"
                  alignItems="center"
                >
                  <StatusBadge status={b.status} />
                  <Box
                    variant="span"
                    color="text-body-secondary"
                    fontSize="body-s"
                  >
                    {b.note}
                  </Box>
                </SpaceBetween>
              </Box>
            ))}
            <Box variant="p" color="text-body-secondary" fontSize="body-s">
              <StatusIndicator type="info">
                Editing an APPROVED record
              </StatusIndicator>{" "}
              creates a new DRAFT revision while the approved revision stays
              discoverable until the new one is approved (dual-revision).
              Management APIs show the latest revision; discovery APIs show only
              the approved one.
            </Box>
            <Box fontSize="body-s">
              <Link href={LIFECYCLE_DOC} external>
                AWS record lifecycle documentation
              </Link>
            </Box>
          </SpaceBetween>
        </ExpandableSection>
      </SpaceBetween>
    </Container>
  );
}
