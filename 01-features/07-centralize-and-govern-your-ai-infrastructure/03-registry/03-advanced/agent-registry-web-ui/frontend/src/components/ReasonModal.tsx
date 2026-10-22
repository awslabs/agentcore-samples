// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { useState } from "react";
import Modal from "@cloudscape-design/components/modal";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import SpaceBetween from "@cloudscape-design/components/space-between";
import FormField from "@cloudscape-design/components/form-field";
import Textarea from "@cloudscape-design/components/textarea";

interface Props {
  visible: boolean;
  title: string;
  actionLabel: string;
  reasonRequired?: boolean;
  onDismiss: () => void;
  onConfirm: (reason: string) => Promise<void>;
}

export default function ReasonModal({
  visible,
  title,
  actionLabel,
  reasonRequired = true,
  onDismiss,
  onConfirm,
}: Props) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const confirm = async () => {
    setBusy(true);
    try {
      await onConfirm(reason);
      setReason("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={title}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={busy}
              disabled={reasonRequired && !reason.trim()}
              onClick={() => void confirm()}
            >
              {actionLabel}
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <FormField
        label="Reason"
        description={
          reasonRequired
            ? "Required — recorded on the record's status history."
            : "Optional."
        }
      >
        <Textarea
          value={reason}
          onChange={({ detail }) => setReason(detail.value)}
          rows={3}
        />
      </FormField>
    </Modal>
  );
}
