// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import AttributeEditor from "@cloudscape-design/components/attribute-editor";
import Input from "@cloudscape-design/components/input";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import {
  MAX_TAGS,
  validateTagKey,
  validateTagValue,
  type TagPair,
} from "./tags";

interface Props {
  value: TagPair[];
  onChange: (pairs: TagPair[]) => void;
}

/**
 * Key/value tag editor for a registry record. Tags are applied directly by
 * `CreateRegistryRecord`, so no follow-up call is needed on create.
 */
export default function TagsEditor({ value, onChange }: Props) {
  const keys = value.map((p) => p.key);

  return (
    <SpaceBetween size="xs">
      <AttributeEditor<TagPair>
        items={value}
        onAddButtonClick={() => onChange([...value, { key: "", value: "" }])}
        onRemoveButtonClick={({ detail }) =>
          onChange(value.filter((_, i) => i !== detail.itemIndex))
        }
        addButtonText="Add tag"
        removeButtonText="Remove"
        isItemRemovable={() => true}
        empty="No tags associated with this record."
        additionalInfo={
          <Box variant="span" color="text-body-secondary" fontSize="body-s">
            Up to {MAX_TAGS} tags. Keys and values allow letters, digits, spaces
            and <Box variant="code">. _ : / = + - @</Box>. Tags are metadata for
            ownership, cost allocation and governance — they are not a discovery
            search filter.
          </Box>
        }
        definition={[
          {
            label: "Key",
            control: (item, index) => (
              <Input
                value={item.key}
                placeholder="owner"
                ariaLabel={`Tag key ${index + 1}`}
                onChange={({ detail }) => {
                  const next = [...value];
                  next[index] = { ...item, key: detail.value };
                  onChange(next);
                }}
                invalid={
                  Boolean(item.key) && Boolean(validateTagKey(item.key, keys))
                }
              />
            ),
            errorText: (item) =>
              item.key ? validateTagKey(item.key, keys) : undefined,
          },
          {
            label: "Value",
            control: (item, index) => (
              <Input
                value={item.value}
                placeholder="platform-team"
                ariaLabel={`Tag value ${index + 1}`}
                onChange={({ detail }) => {
                  const next = [...value];
                  next[index] = { ...item, value: detail.value };
                  onChange(next);
                }}
                invalid={Boolean(validateTagValue(item.value))}
              />
            ),
            errorText: (item) => validateTagValue(item.value),
          },
        ]}
      />
    </SpaceBetween>
  );
}
