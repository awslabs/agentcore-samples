// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import FormField from "@cloudscape-design/components/form-field";
import Multiselect, { type MultiselectProps } from "@cloudscape-design/components/multiselect";
import Select, { type SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import type { FilterSelections, MetadataField } from "./customMetadata";

interface Props {
  /** Output of `filterableFields`: Enum and Boolean fields only. */
  fields: MetadataField[];
  value: FilterSelections;
  onChange: (next: FilterSelections) => void;
}

const ANY: SelectProps.Option = { label: "Any", value: "" };
const TRUE_OPTION: SelectProps.Option = { label: "True", value: "true" };
const FALSE_OPTION: SelectProps.Option = { label: "False", value: "false" };
const BOOLEAN_OPTIONS = [ANY, TRUE_OPTION, FALSE_OPTION];

/** The next selections with `name` set, or removed ("any") when `next` is undefined. */
function withSelection(
  value: FilterSelections,
  name: string,
  next: string[] | boolean | undefined,
): FilterSelections {
  const out = { ...value };
  if (next === undefined) delete out[name];
  else out[name] = next;
  return out;
}

/**
 * Custom metadata filter controls for discovery search (Req 10.1, 10.2).
 * Enum fields use a `Multiselect`; Boolean fields use a `Select` with
 * "Any" / "True" / "False". No selection or "Any" removes the key, so it adds
 * no condition. Stateless: the page owns `value`.
 */
export default function CustomMetadataFilters({ fields, value, onChange }: Props) {
  const set = (name: string, next: string[] | boolean | undefined) =>
    onChange(withSelection(value, name, next));

  return (
    <SpaceBetween direction="horizontal" size="l">
      {fields.map((field) => {
        const label = field.title ?? field.name;
        const controlId = `custom-metadata-filter-${field.name}`;
        const current = value[field.name];
        let control: JSX.Element | null = null;

        if (field.type === "enum") {
          const options: MultiselectProps.Option[] = (field.enumValues ?? []).map((v) => ({
            label: v,
            value: v,
          }));
          const chosen = Array.isArray(current) ? current : [];
          control = (
            <Multiselect
              controlId={controlId}
              selectedOptions={options.filter((o) => o.value && chosen.includes(o.value))}
              options={options}
              placeholder="Any"
              onChange={({ detail }) => {
                const picked = detail.selectedOptions
                  .map((o) => o.value)
                  .filter((v): v is string => typeof v === "string" && v !== "");
                set(field.name, picked.length > 0 ? picked : undefined);
              }}
            />
          );
        } else if (field.type === "boolean") {
          const selected =
            current === true ? TRUE_OPTION : current === false ? FALSE_OPTION : ANY;
          control = (
            <Select
              controlId={controlId}
              selectedOption={selected}
              options={BOOLEAN_OPTIONS}
              onChange={({ detail }) => {
                const v = detail.selectedOption.value;
                set(field.name, v === "true" ? true : v === "false" ? false : undefined);
              }}
            />
          );
        }

        if (!control) return null;
        return (
          <FormField key={field.name} controlId={controlId} label={label}>
            {control}
          </FormField>
        );
      })}
    </SpaceBetween>
  );
}
