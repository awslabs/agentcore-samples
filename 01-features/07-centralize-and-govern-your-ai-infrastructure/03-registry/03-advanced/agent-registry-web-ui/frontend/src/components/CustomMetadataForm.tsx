// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import Box from "@cloudscape-design/components/box";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Select, { type SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import type {
  MetadataError,
  MetadataField,
  MetadataValue,
} from "./customMetadata";

interface Props {
  fields: MetadataField[];
  value: Record<string, MetadataValue>;
  onChange: (next: Record<string, MetadataValue>) => void;
  errors: MetadataError[];
}

const NOT_SET: SelectProps.Option = { label: "Not set", value: "" };
const TRUE_OPTION: SelectProps.Option = { label: "True", value: "true" };
const FALSE_OPTION: SelectProps.Option = { label: "False", value: "false" };
const BOOLEAN_OPTIONS = [NOT_SET, TRUE_OPTION, FALSE_OPTION];

/** The next form state with `name` set, or removed when `next` is undefined. */
function withValue(
  value: Record<string, MetadataValue>,
  name: string,
  next: MetadataValue | undefined,
): Record<string, MetadataValue> {
  const out = { ...value };
  if (next === undefined) delete out[name];
  else out[name] = next;
  return out;
}

/**
 * Inputs for a record's custom metadata, one per field of the selected record
 * type's Effective_Field_Set. Stateless: the page owns `value` and `errors`.
 * Entered text is kept as typed; trimming happens in `toCustomMetadata`.
 */
export default function CustomMetadataForm({ fields, value, onChange, errors }: Props) {
  const set = (name: string, next: MetadataValue | undefined) =>
    onChange(withValue(value, name, next));

  return (
    <SpaceBetween size="l">
      {fields.map((field) => {
        const label = field.title ?? field.name;
        const errorText = errors
          .filter((e) => e.field === field.name)
          .map((e) => e.message)
          .join(". ");
        const current = value[field.name];
        const controlId = `custom-metadata-${field.name}`;

        let control: JSX.Element;
        if (field.type === "enum") {
          const options: SelectProps.Option[] = [
            NOT_SET,
            ...(field.enumValues ?? []).map((v) => ({ label: v, value: v })),
          ];
          const selected =
            typeof current === "string" && current !== ""
              ? (options.find((o) => o.value === current) ?? {
                  label: current,
                  value: current,
                })
              : NOT_SET;
          control = (
            <Select
              controlId={controlId}
              selectedOption={selected}
              options={options}
              invalid={Boolean(errorText)}
              onChange={({ detail }) =>
                set(field.name, detail.selectedOption.value || undefined)
              }
            />
          );
        } else if (field.type === "boolean") {
          const selected =
            current === true ? TRUE_OPTION : current === false ? FALSE_OPTION : NOT_SET;
          control = (
            <Select
              controlId={controlId}
              selectedOption={selected}
              options={BOOLEAN_OPTIONS}
              invalid={Boolean(errorText)}
              onChange={({ detail }) => {
                const v = detail.selectedOption.value;
                set(field.name, v === "true" ? true : v === "false" ? false : undefined);
              }}
            />
          );
        } else {
          control = (
            <Input
              controlId={controlId}
              type={field.type === "url" ? "url" : "text"}
              value={typeof current === "string" ? current : ""}
              placeholder={field.type === "url" ? "https://example.com" : undefined}
              invalid={Boolean(errorText)}
              onChange={({ detail }) =>
                set(field.name, detail.value === "" ? undefined : detail.value)
              }
            />
          );
        }

        return (
          <FormField
            key={field.name}
            controlId={controlId}
            label={
              <span>
                {label}
                {field.required && (
                  <Box variant="span" color="text-body-secondary" fontSize="body-s">
                    {" "}
                    (required)
                  </Box>
                )}
              </span>
            }
            description={field.description}
            errorText={errorText || undefined}
          >
            {control}
          </FormField>
        );
      })}
    </SpaceBetween>
  );
}
