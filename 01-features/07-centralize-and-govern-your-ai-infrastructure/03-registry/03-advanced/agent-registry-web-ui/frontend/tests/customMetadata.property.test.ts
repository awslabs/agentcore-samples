// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Property-based tests for the custom metadata logic module. Schemas are
// generated as plain specs (field list + required list), rendered to the wire
// shape with JSON.stringify, and compared against a small reference model.
import { describe, it, expect } from "vitest";
import fc from "fast-check";
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
import {
  buildMetadataUpdate,
  buildSearchFilter,
  displayValues,
  filterableFields,
  hasValue,
  parseSchema,
  pickFields,
  toCustomMetadata,
  validateMetadata,
  type CustomMetadata,
  type FieldType,
  type FilterSelections,
  type MetadataField,
  type MetadataValue,
} from "../src/components/customMetadata";

const RUNS = { numRuns: 100 };

// ---------------------------------------------------------------------------
// Shared arbitraries (reused by later properties in this file)
// ---------------------------------------------------------------------------

/** A field name the service accepts: a letter, then letters, digits, `_` or `-`. */
export const fieldNameArb = fc.stringMatching(/^[A-Za-z][A-Za-z0-9_-]{0,15}$/);

/** Distinct field names, deduplicated case-insensitively. */
export const fieldNamesArb = (minLength: number, maxLength: number) =>
  fc.uniqueArray(fieldNameArb, {
    minLength,
    maxLength,
    selector: (n) => n.toLowerCase(),
  });

export const fieldTypeArb: fc.Arbitrary<FieldType> = fc.constantFrom(
  "text",
  "enum",
  "url",
  "boolean",
);

/** One enum value. */
export const enumValueArb = fc.stringMatching(/^[a-z0-9][a-z0-9-]{0,9}$/);

/** 1 to 5 distinct enum values. */
export const enumListArb = fc.uniqueArray(enumValueArb, { minLength: 1, maxLength: 5 });

/** Record types used for overrides and lookups ("OTHER" exercises a non-base type). */
export const recordTypeArb = fc.constantFrom("MCP", "AGENT", "SKILL", "CUSTOM", "OTHER");

/** A field as the test generates it (before rendering to JSON Schema). */
export interface FieldSpec {
  name: string;
  type: FieldType;
  enumValues?: string[];
}

/** One schema: fields in declaration order plus the `required` list. */
export interface SchemaSpec {
  fields: FieldSpec[];
  required: string[];
}

export interface ConfigSpec {
  defaultSchema?: SchemaSpec;
  overrides: { recordType: string; schema: SchemaSpec }[];
}

const fieldSpecArb = (name: string): fc.Arbitrary<FieldSpec> =>
  fieldTypeArb.chain((type): fc.Arbitrary<FieldSpec> =>
    type === "enum"
      ? enumListArb.map((enumValues) => ({ name, type, enumValues }))
      : fc.constant({ name, type }),
  );

/** A subset of names to mark required. */
const requiredArb = (names: string[]) => fc.subarray(names);

/** A default schema with 0 to 8 fields. */
export const defaultSchemaSpecArb: fc.Arbitrary<SchemaSpec> = fieldNamesArb(0, 8).chain(
  (names) =>
    fc.record({
      fields: fc.tuple(...names.map(fieldSpecArb)).map((fs) => fs as FieldSpec[]),
      required: requiredArb(names),
    }),
);

/**
 * An override for a given default schema: repeats a subset of the common
 * fields (same type; enum lists are a shuffled superset of the common values)
 * and adds new fields whose names do not collide with any common name.
 */
export const overrideSpecArb = (common: FieldSpec[]): fc.Arbitrary<SchemaSpec> => {
  const commonLower = new Set(common.map((f) => f.name.toLowerCase()));
  const repeatedArb = fc.subarray(common).chain((picked) =>
    fc.tuple(
      ...picked.map((f): fc.Arbitrary<FieldSpec> => {
        if (f.type !== "enum") return fc.constant({ name: f.name, type: f.type });
        const base = f.enumValues ?? [];
        return fc
          .uniqueArray(enumValueArb, { maxLength: 3 })
          .chain((extra) => {
            const all = Array.from(new Set([...base, ...extra]));
            return fc.shuffledSubarray(all, { minLength: all.length, maxLength: all.length });
          })
          .map((enumValues) => ({ name: f.name, type: "enum" as const, enumValues }));
      }),
    ),
  );
  const newArb = fieldNamesArb(0, 5)
    .map((names) => names.filter((n) => !commonLower.has(n.toLowerCase())))
    .chain((names) => fc.tuple(...names.map(fieldSpecArb)));

  return fc
    .tuple(repeatedArb, newArb)
    .chain(([repeated, added]) =>
      // Interleave repeated and new fields in a random declaration order.
      fc
        .shuffledSubarray([...repeated, ...added] as FieldSpec[], {
          minLength: repeated.length + added.length,
          maxLength: repeated.length + added.length,
        })
        .chain((fields) =>
          requiredArb(fields.map((f) => f.name)).map((required) => ({ fields, required })),
        ),
    );
};

/** A full config: optional default schema plus overrides for distinct record types. */
export const configSpecArb: fc.Arbitrary<ConfigSpec> = fc
  .option(defaultSchemaSpecArb, { nil: undefined })
  .chain((defaultSchema) =>
    fc
      .uniqueArray(recordTypeArb, { maxLength: 5 })
      .chain((types) =>
        fc.tuple(...types.map(() => overrideSpecArb(defaultSchema?.fields ?? []))).map(
          (schemas) => ({
            defaultSchema,
            overrides: types.map((recordType, i) => ({
              recordType,
              schema: schemas[i] as SchemaSpec,
            })),
          }),
        ),
      ),
  );

/** Render one field to its JSON Schema property definition. */
export function fieldToJson(f: FieldSpec): Record<string, unknown> {
  switch (f.type) {
    case "text":
      return { type: "string" };
    case "url":
      return { type: "string", format: "uri" };
    case "boolean":
      return { type: "boolean" };
    case "enum":
      return { type: "string", enum: f.enumValues };
  }
}

/** Render a schema spec to the JSON string the service stores. */
export function schemaToString(s: SchemaSpec): string {
  const properties: Record<string, unknown> = {};
  for (const f of s.fields) properties[f.name] = fieldToJson(f);
  return JSON.stringify({ type: "object", properties, required: s.required });
}

/** Render a config spec to the SDK `CustomMetadataSchemaConfiguration`. */
export function toConfig(spec: ConfigSpec): CustomMetadataSchemaConfiguration {
  const config: CustomMetadataSchemaConfiguration = {};
  if (spec.defaultSchema) config.defaultSchema = schemaToString(spec.defaultSchema);
  if (spec.overrides.length > 0) {
    config.recordTypeSchemaOverrides = spec.overrides.map((o) => ({
      recordType: o.recordType as never,
      schema: schemaToString(o.schema),
    }));
  }
  return config;
}

/** Reference model of the Effective_Field_Set (Req 4.1, 4.2, 4.4). */
export function modelFields(spec: ConfigSpec, recordType: string): MetadataField[] {
  const out: MetadataField[] = [];
  const d = spec.defaultSchema;
  for (const f of d?.fields ?? []) {
    const field: MetadataField = {
      name: f.name,
      type: f.type,
      required: d!.required.includes(f.name),
      source: "common",
    };
    if (f.enumValues) field.enumValues = [...f.enumValues];
    out.push(field);
  }
  const o = spec.overrides.find((x) => x.recordType === recordType)?.schema;
  for (const f of o?.fields ?? []) {
    const req = o!.required.includes(f.name);
    const existing = out.find((x) => x.name === f.name);
    if (existing) {
      existing.required = existing.required || req;
      if (existing.enumValues) {
        existing.enumValues = Array.from(
          new Set([...existing.enumValues, ...(f.enumValues ?? [])]),
        );
      }
      continue;
    }
    const field: MetadataField = { name: f.name, type: f.type, required: req, source: "recordType" };
    if (f.enumValues) field.enumValues = [...f.enumValues];
    out.push(field);
  }
  return out;
}

/** An Effective_Field_Set: a generated config parsed for a generated record type. */
export const fieldSetArb: fc.Arbitrary<MetadataField[]> = fc
  .tuple(configSpecArb, recordTypeArb)
  .map(([spec, recordType]) => {
    const result = parseSchema(toConfig(spec), recordType);
    return result.ok ? result.fields : [];
  });

/** Optional surrounding whitespace the form may carry before trimming. */
const paddingArb = fc.constantFrom("", " ", "  ", "\t", "\n", " \t ");

/** Text input: 1 to 128 characters after trimming, with optional padding. */
export const textInputArb: fc.Arbitrary<string> = fc
  .tuple(
    paddingArb,
    fc.string({ minLength: 1, maxLength: 128 }).filter((s) => s.trim().length > 0),
    paddingArb,
  )
  .map(([pre, core, post]) => pre + core + post);

/** URL input: an absolute URI of at most 128 characters, with optional padding. */
export const urlInputArb: fc.Arbitrary<string> = fc
  .tuple(
    paddingArb,
    fc.oneof(
      fc.webUrl().filter((u) => u.length <= 128),
      fc
        .tuple(
          fc.stringMatching(/^[A-Za-z][A-Za-z0-9+.-]{0,9}$/),
          fc.stringMatching(/^[A-Za-z0-9/._~@?=&-]{1,60}$/),
        )
        .map(([scheme, rest]) => `${scheme}:${rest}`),
    ),
    paddingArb,
  )
  .map(([pre, core, post]) => pre + core + post);

/** An in-range input that Has_Value for one field. */
export const filledInputArb = (f: MetadataField): fc.Arbitrary<MetadataValue> => {
  switch (f.type) {
    case "text":
      return textInputArb;
    case "url":
      return urlInputArb;
    case "enum":
      return fc.constantFrom(...(f.enumValues ?? []));
    case "boolean":
      return fc.boolean();
  }
};

/** Form input for a field with no value: absent, or (Text/URL) empty or blank text. */
const emptyInputArb = (f: MetadataField): fc.Arbitrary<MetadataValue | undefined> =>
  f.type === "text" || f.type === "url"
    ? fc.constantFrom(undefined, "", " ", "\t ")
    : fc.constant(undefined);

/** Build a form state object, dropping absent (`undefined`) entries. */
const toForm = (
  fields: MetadataField[],
  inputs: (MetadataValue | undefined)[],
): Record<string, MetadataValue | undefined> => {
  const form: Record<string, MetadataValue | undefined> = {};
  fields.forEach((f, i) => {
    if (inputs[i] !== undefined) form[f.name] = inputs[i];
  });
  return form;
};

/**
 * A field set with an in-range form state: either every field empty, or every
 * required field filled and each optional field filled or empty at random.
 */
export const validFormCaseArb = fieldSetArb.chain((fields) =>
  fc.boolean().chain((allEmpty) =>
    fc
      .tuple(
        ...fields.map((f): fc.Arbitrary<MetadataValue | undefined> => {
          if (allEmpty) return emptyInputArb(f);
          if (f.required) return filledInputArb(f);
          return fc.oneof(filledInputArb(f), emptyInputArb(f));
        }),
      )
      .map((inputs) => ({ fields, allEmpty, form: toForm(fields, inputs) })),
  ),
);

// ---------------------------------------------------------------------------
// Properties
// ---------------------------------------------------------------------------

describe("customMetadata properties", () => {
  // Feature: registry-custom-metadata, Property 1: Parser matches the merge model and is deterministic
  it("Property 1: parser matches the merge model and is deterministic", () => {
    // **Validates: Requirements 4.1, 4.2, 4.4, 4.6**
    fc.assert(
      fc.property(configSpecArb, recordTypeArb, (spec, recordType) => {
        const config = toConfig(spec);
        const first = parseSchema(config, recordType);
        const second = parseSchema(config, recordType);

        expect(first.ok).toBe(true);
        if (!first.ok) return;

        // Matches the reference merge model (order, enum union, required OR, source).
        expect(first.fields).toEqual(modelFields(spec, recordType));

        // A type with no override yields only common fields.
        const override = spec.overrides.find((o) => o.recordType === recordType);
        if (!override) {
          expect(first.fields.every((f) => f.source === "common")).toBe(true);
        }

        // Field names are unique.
        const names = first.fields.map((f) => f.name);
        expect(new Set(names).size).toBe(names.length);

        // Every required field is required in the default schema or this type's override.
        for (const f of first.fields.filter((x) => x.required)) {
          const inDefault = spec.defaultSchema?.required.includes(f.name) ?? false;
          const inOverride = override?.schema.required.includes(f.name) ?? false;
          expect(inDefault || inOverride).toBe(true);
        }

        // Deterministic.
        expect(second).toEqual(first);
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 2: Invalid schemas fail with a named error and no fields
  it("Property 2: invalid schemas fail with a named error and no fields", () => {
    // **Validates: Requirements 4.5**
    type Mutation =
      | { kind: "nonJson"; raw: string }
      | { kind: "nonObject"; raw: string }
      | { kind: "badProperty"; name: string; def: Record<string, unknown> };

    const mutationArb: fc.Arbitrary<Mutation> = fc.oneof(
      fc
        .constantFrom("", "{", "not json", '{"type":', "{'type':'object'}")
        .map((raw) => ({ kind: "nonJson" as const, raw })),
      fc
        .constantFrom("[]", "null", "42", '"object"', "{}", '{"type":"string"}', '{"type":"array"}')
        .map((raw) => ({ kind: "nonObject" as const, raw })),
      fc.record({
        kind: fc.constant("badProperty" as const),
        name: fieldNameArb,
        def: fc.constantFrom<Record<string, unknown>>(
          { type: "number" },
          { type: "integer" },
          { type: "string", pattern: "x" },
          { type: "string", format: "email" },
          { type: "string", enum: [] },
          { type: "array", items: { type: "string" } },
          { type: "object", properties: { inner: { type: "string" } } },
        ),
      }),
    );

    /** Render a schema spec with the mutation applied. */
    const mutate = (s: SchemaSpec, m: Mutation): string => {
      if (m.kind !== "badProperty") return m.raw;
      const doc = JSON.parse(schemaToString(s)) as { properties: Record<string, unknown> };
      doc.properties[m.name] = m.def;
      return JSON.stringify(doc);
    };

    // Pick the schema to break: the default (present or generated) or one override.
    const caseArb = fc
      .tuple(configSpecArb, defaultSchemaSpecArb, mutationArb, recordTypeArb, fc.nat())
      .chain(([spec, fallbackDefault, mutation, lookupType, pick]) => {
        const targets = spec.overrides.length;
        return fc.boolean().map((targetDefault) => {
          const useDefault = targetDefault || targets === 0;
          return {
            spec: useDefault ? { ...spec, defaultSchema: spec.defaultSchema ?? fallbackDefault } : spec,
            mutation,
            target: useDefault ? ("default" as const) : pick % targets,
            lookupType,
          };
        });
      });

    fc.assert(
      fc.property(caseArb, ({ spec, mutation, target, lookupType }) => {
        const config = toConfig(spec);
        let recordType: string;
        let label: string;
        if (target === "default") {
          config.defaultSchema = mutate(spec.defaultSchema!, mutation);
          // A bad default affects every record type.
          recordType = lookupType;
          label = "default schema";
        } else {
          const o = spec.overrides[target];
          config.recordTypeSchemaOverrides![target].schema = mutate(o.schema, mutation);
          // A bad override only affects its own record type.
          recordType = o.recordType;
          label = `${o.recordType} schema`;
        }

        const result = parseSchema(config, recordType);
        expect(result.ok).toBe(false);
        if (result.ok) return;
        expect("fields" in result).toBe(false);
        expect(result.error.startsWith(`${label}:`)).toBe(true);
        if (mutation.kind === "badProperty") {
          expect(result.error).toContain(`"${mutation.name}"`);
        }
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 3: Form output always passes validation
  it("Property 3: form output always passes validation", () => {
    // **Validates: Requirements 5.7, 5.9**
    fc.assert(
      fc.property(validFormCaseArb, ({ fields, allEmpty, form }) => {
        const payload = toCustomMetadata(fields, form);
        expect(validateMetadata(fields, payload)).toEqual([]);

        if (allEmpty) {
          // No field Has_Value: nothing is sent and nothing is reported.
          expect(Object.values(form).some(hasValue)).toBe(false);
          expect(payload).toEqual({});
          expect(validateMetadata(fields, form)).toEqual([]);
        }
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 4: Every injected fault is reported in one pass
  it("Property 4: every injected fault is reported in one pass", () => {
    // **Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8**
    type Fault =
      | { kind: "clearRequired" }
      | { kind: "tooLong" | "noScheme" | "badEnum" | "wrongType"; value: unknown };

    const padded = (core: string) =>
      paddingArb.chain((pre) => paddingArb.map((post) => pre + core + post));

    /** The faults that apply to one field, each producing a replacement value. */
    const faultArb = (f: MetadataField): fc.Arbitrary<Fault> => {
      const options: fc.Arbitrary<Fault>[] = [
        // Wrong value type: a string for a Boolean field, a boolean for the rest.
        (f.type === "boolean" ? fc.constantFrom<unknown>("true", "false", "yes") : fc.boolean()).map(
          (value): Fault => ({ kind: "wrongType", value }),
        ),
      ];
      if (f.required) options.push(fc.constant<Fault>({ kind: "clearRequired" }));
      if (f.type === "text") {
        options.push(
          fc
            .integer({ min: 129, max: 200 })
            .chain((n) => padded("a".repeat(n)))
            .map((value): Fault => ({ kind: "tooLong", value })),
        );
      }
      if (f.type === "url") {
        options.push(
          fc
            .integer({ min: 129, max: 200 })
            .chain((n) => padded("https://example.com/" + "a".repeat(n - 20)))
            .map((value): Fault => ({ kind: "tooLong", value })),
          // No colon anywhere, so no scheme.
          fc
            .stringMatching(/^[A-Za-z0-9./_-]{1,40}$/)
            .map((value): Fault => ({ kind: "noScheme", value })),
        );
      }
      if (f.type === "enum") {
        const allowed = f.enumValues ?? [];
        options.push(
          fc
            .string({ minLength: 1, maxLength: 12 })
            .filter((s) => s.trim() !== "" && !allowed.includes(s))
            .map((value): Fault => ({ kind: "badEnum", value })),
        );
      }
      return fc.oneof(...options);
    };

    const caseArb = validFormCaseArb
      .map(({ fields, form }) => ({ fields, payload: toCustomMetadata(fields, form) }))
      .filter(({ payload }) => Object.keys(payload).length > 0)
      .chain(({ fields, payload }) => {
        const names = new Set(fields.map((f) => f.name));
        const faultedFieldsArb = fc
          .subarray(fields)
          .chain((picked) =>
            fc.tuple(...picked.map(faultArb)).map((faults) =>
              picked.map((f, i) => ({ field: f, fault: faults[i] as Fault })),
            ),
          );
        // Unknown keys never collide with a schema field name (case-sensitive).
        const unknownKeysArb = fc.uniqueArray(
          fieldNameArb.filter((n) => !names.has(n)),
          { maxLength: 3 },
        );
        return fc
          .tuple(faultedFieldsArb, unknownKeysArb)
          .filter(([faulted, unknown]) => faulted.length + unknown.length > 0)
          .map(([faulted, unknown]) => ({ fields, payload, faulted, unknown }));
      });

    fc.assert(
      fc.property(caseArb, ({ fields, payload, faulted, unknown }) => {
        const values: Record<string, unknown> = { ...payload };
        for (const { field, fault } of faulted) {
          if (fault.kind === "clearRequired") delete values[field.name];
          else values[field.name] = fault.value;
        }
        for (const key of unknown) values[key] = "x";

        // Required fields are only enforced when some value remains (Req 5.1, 5.7).
        fc.pre(Object.values(values).some(hasValue));

        const errors = validateMetadata(fields, values);
        const errorKeys = new Set(errors.map((e) => e.field));
        const faultedKeys = new Set([...faulted.map((x) => x.field.name), ...unknown]);

        for (const key of faultedKeys) expect(errorKeys.has(key)).toBe(true);
        for (const key of errorKeys) expect(faultedKeys.has(key)).toBe(true);
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 5: Payload and form restriction keep exactly the right keys
  it("Property 5: payload and form restriction keep exactly the right keys", () => {
    // **Validates: Requirements 6.4, 6.5, 7.1, 7.2**

    /** Any value a form or the service may hold: filled, blank, padded or boolean. */
    const anyValueArb: fc.Arbitrary<MetadataValue> = fc.oneof(
      textInputArb,
      fc.constantFrom("", " ", "\t ", "\n"),
      fc.boolean(),
    );

    /** Extra keys that are not in the field set (case-sensitive). */
    const extraKeysArb = (fields: MetadataField[]) => {
      const names = new Set(fields.map((f) => f.name));
      return fc.uniqueArray(
        fieldNameArb.filter((n) => !names.has(n)),
        { maxLength: 4 },
      );
    };

    /** Values for every field (enum fields may hold an allowed value), absent allowed. */
    const valuesFor = (fields: MetadataField[]) =>
      fc.tuple(
        ...fields.map((f) =>
          fc.option(
            f.type === "enum" ? fc.oneof(filledInputArb(f), anyValueArb) : anyValueArb,
            { nil: undefined },
          ),
        ),
      );

    const caseArb = fieldSetArb.chain((fields) =>
      fc
        .tuple(
          valuesFor(fields),
          extraKeysArb(fields).chain((keys) =>
            fc.tuple(...keys.map(() => anyValueArb)).map((vals) =>
              keys.map((k, i) => [k, vals[i] as MetadataValue] as const),
            ),
          ),
          valuesFor(fields),
        )
        .map(([formInputs, extras, storedInputs]) => {
          const form: Record<string, MetadataValue | undefined> = {};
          const stored: Record<string, MetadataValue> = {};
          fields.forEach((f, i) => {
            // Keep `undefined` entries in the form: the form may hold them.
            if (formInputs[i] !== undefined || i % 2 === 0) form[f.name] = formInputs[i];
            const s = storedInputs[i];
            if (s !== undefined) stored[f.name] = s;
          });
          for (const [k, v] of extras) {
            form[k] = v;
            stored[k] = v;
          }
          return { fields, form, stored };
        }),
    );

    fc.assert(
      fc.property(caseArb, ({ fields, form, stored }) => {
        // toCustomMetadata: exactly the set fields that Has_Value (Req 6.5, 7.2).
        const payload = toCustomMetadata(fields, form);
        const expected: Record<string, MetadataValue> = {};
        for (const f of fields) {
          const v = form[f.name];
          if (!hasValue(v)) continue;
          if (typeof v === "boolean") expected[f.name] = v;
          else if (typeof v === "string") {
            expected[f.name] = f.type === "text" || f.type === "url" ? v.trim() : v;
          }
        }
        expect(payload).toEqual(expected);
        expect(Object.keys(payload).sort()).toEqual(Object.keys(expected).sort());
        for (const f of fields) {
          const v = form[f.name];
          if (typeof v === "boolean") expect(payload[f.name]).toBe(v);
        }

        // pickFields: exactly the stored entries whose key is in the set (Req 6.4, 7.1).
        const names = new Set(fields.map((f) => f.name));
        const picked = pickFields(fields, stored);
        const expectedPicked = Object.fromEntries(
          Object.entries(stored).filter(([k]) => names.has(k)),
        );
        expect(picked).toEqual(expectedPicked);
        expect(Object.keys(picked).sort()).toEqual(Object.keys(expectedPicked).sort());
        for (const k of Object.keys(picked)) expect(picked[k]).toBe(stored[k]);
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 6: Update member follows the omit / clear / replace model
  it("Property 6: update member follows the omit / clear / replace model", () => {
    // **Validates: Requirements 7.3, 7.4, 7.5, 7.6**

    /** Values that include the string forms of booleans, so type-only changes occur. */
    const valueArb: fc.Arbitrary<MetadataValue> = fc.oneof(
      fc.boolean(),
      fc.constantFrom("true", "false", "a", "b"),
      enumValueArb,
    );

    /** A stored metadata map with 0 to 5 entries. */
    const metadataArb: fc.Arbitrary<CustomMetadata> = fieldNamesArb(0, 5).chain((keys) =>
      fc
        .tuple(...keys.map(() => valueArb))
        .map((vals) => Object.fromEntries(keys.map((k, i) => [k, vals[i] as MetadataValue]))),
    );

    /** A value whose type can flip without changing its text: true, false, "true", "false". */
    const isFlippable = (v: MetadataValue | undefined) =>
      typeof v === "boolean" || v === "true" || v === "false";

    /** Flip the type only: "true" <-> true, "false" <-> false. */
    const flipType = (v: MetadataValue): MetadataValue =>
      typeof v === "boolean" ? String(v) : v === "true";

    /** Derive an edited map from the original, covering each kind of change. */
    const editedArb = (original: CustomMetadata): fc.Arbitrary<CustomMetadata> => {
      const keys = Object.keys(original);
      const options: fc.Arbitrary<CustomMetadata>[] = [
        // Unrelated map (random keys and values; often a different key set).
        metadataArb,
        // Empty edit.
        fc.constant({}),
        // Equal copy, in a shuffled key order.
        fc
          .shuffledSubarray(keys, { minLength: keys.length, maxLength: keys.length })
          .map((ks) => Object.fromEntries(ks.map((k) => [k, original[k] as MetadataValue]))),
        // Added key (differing key set).
        fc
          .tuple(fieldNameArb.filter((n) => !(n in original)), valueArb)
          .map(([k, v]) => ({ ...original, [k]: v })),
      ];
      const flippable = keys.filter((k) => isFlippable(original[k]));
      if (flippable.length > 0) {
        // Same keys, values differing only in type ("true" vs true), one or more keys.
        options.push(
          fc.subarray(flippable, { minLength: 1 }).map((ks) => {
            const copy = { ...original };
            for (const k of ks) copy[k] = flipType(original[k] as MetadataValue);
            return copy;
          }),
        );
      }
      if (keys.length > 0) {
        const keyArb = fc.constantFrom(...keys);
        options.push(
          // Same keys, one value replaced at random (may or may not differ).
          fc.tuple(keyArb, valueArb).map(([k, v]) => ({ ...original, [k]: v })),
          // Removed key (differing key set).
          keyArb.map((k) => {
            const copy = { ...original };
            delete copy[k];
            return copy;
          }),
        );
      }
      return fc.oneof(...options);
    };

    const caseArb = fc
      .oneof(
        fc.constant<CustomMetadata | undefined>(undefined),
        fc.constant<CustomMetadata | undefined>({}),
        metadataArb,
      )
      .chain((original) =>
        editedArb(original ?? {}).map((edited) => ({ original, edited })),
      );

    /** Reference equality: same key set and `===` per key. */
    const sameMetadata = (a: CustomMetadata, b: CustomMetadata): boolean => {
      const ak = Object.keys(a).sort();
      const bk = Object.keys(b).sort();
      return ak.length === bk.length && ak.every((k, i) => k === bk[i] && a[k] === b[k]);
    };

    fc.assert(
      fc.property(caseArb, ({ original, edited }) => {
        const base = original ?? {};
        const result = buildMetadataUpdate(original, edited);

        if (sameMetadata(base, edited)) {
          // Req 7.3: unchanged (absent treated as {}) omits the member.
          expect(result).toBeUndefined();
        } else if (Object.keys(edited).length === 0) {
          // Req 7.5, 7.6: everything cleared against a non-empty original sends {}.
          expect(Object.keys(base).length).toBeGreaterThan(0);
          expect(result).toEqual({ optionalValue: {} });
        } else {
          // Req 7.4: any other change sends the whole edited map, types preserved.
          expect(result).toEqual({ optionalValue: edited });
          const sent = result?.optionalValue as CustomMetadata;
          for (const k of Object.keys(edited)) expect(sent[k]).toBe(edited[k]);
        }
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 7: Filter structure and leaf invariant
  it("Property 7: filter structure and leaf invariant", () => {
    // **Validates: Requirements 10.1, 10.2, 10.4, 10.5, 10.6, 10.7, 10.12**

    /** Every field name and enum value the config mentions, for realistic selections. */
    const mentioned = (spec: ConfigSpec) => {
      const all = [
        ...(spec.defaultSchema?.fields ?? []),
        ...spec.overrides.flatMap((o) => o.schema.fields),
      ];
      return {
        names: Array.from(new Set(all.map((f) => f.name))),
        values: Array.from(new Set(all.flatMap((f) => f.enumValues ?? []))),
      };
    };

    /** One selection: enum values (known, disallowed, duplicated or none) or a boolean. */
    const selectionArb = (knownValues: string[]): fc.Arbitrary<string[] | boolean> => {
      const valueArb =
        knownValues.length > 0
          ? fc.oneof(fc.constantFrom(...knownValues), enumValueArb)
          : enumValueArb;
      return fc.oneof(fc.boolean(), fc.constant([]), fc.array(valueArb, { maxLength: 6 }));
    };

    const caseArb = configSpecArb.chain((spec) => {
      const { names, values } = mentioned(spec);
      const keyArb = names.length > 0 ? fc.oneof(fc.constantFrom(...names), fieldNameArb) : fieldNameArb;
      return fc
        .tuple(
          fc.oneof(fc.constant(""), recordTypeArb),
          fc.uniqueArray(keyArb, { maxLength: 10 }).chain((keys) =>
            fc
              .tuple(...keys.map(() => selectionArb(values)))
              .map((sels) =>
                Object.fromEntries(keys.map((k, i) => [k, sels[i]])) as FilterSelections,
              ),
          ),
        )
        .map(([recordType, selections]) => ({ spec, recordType, selections }));
    });

    const isFilterableType = (f: MetadataField) => f.type === "enum" || f.type === "boolean";

    fc.assert(
      fc.property(caseArb, ({ spec, recordType, selections }) => {
        const config = toConfig(spec);
        const fields = filterableFields(config, recordType);

        // Req 10.1: Enum and Boolean fields only, common-only for "all types".
        const source =
          recordType === ""
            ? modelFields({ defaultSchema: spec.defaultSchema, overrides: [] }, "")
            : modelFields(spec, recordType);
        expect(fields).toEqual(source.filter(isFilterableType));
        if (recordType === "") expect(fields.every((f) => f.source === "common")).toBe(true);

        const f = buildSearchFilter(recordType, fields, selections);

        // Expected leaf count (Req 10.2: no selection, [] or disallowed-only adds nothing).
        let expectedLeaves = recordType === "" ? 0 : 1;
        for (const field of fields) {
          if (!Object.prototype.hasOwnProperty.call(selections, field.name)) continue;
          const sel = selections[field.name];
          if (field.type === "boolean" && typeof sel === "boolean") expectedLeaves++;
          if (
            field.type === "enum" &&
            Array.isArray(sel) &&
            sel.some((v) => (field.enumValues ?? []).includes(v))
          ) {
            expectedLeaves++;
          }
        }

        // Req 10.4-10.6: undefined, bare leaf, or a single $and.
        let leaves: Record<string, unknown>[];
        if (expectedLeaves === 0) {
          expect(f).toBeUndefined();
          leaves = [];
        } else if (expectedLeaves === 1) {
          expect(f).toBeDefined();
          expect(f && "$and" in f).toBe(false);
          leaves = [f as Record<string, unknown>];
        } else {
          expect(f).toBeDefined();
          expect(Object.keys(f!)).toEqual(["$and"]);
          const and = (f as { $and: unknown }).$and;
          expect(Array.isArray(and)).toBe(true);
          leaves = and as Record<string, unknown>[];
        }
        expect(leaves.length).toBe(expectedLeaves);

        // Req 10.7, 10.12: leaf keys, operators and values.
        const byName = new Map(fields.map((x) => [x.name, x]));
        const seenKeys = new Set<string>();
        for (const leaf of leaves) {
          const keys = Object.keys(leaf);
          expect(keys.length).toBe(1);
          const key = keys[0];
          expect(seenKeys.has(key)).toBe(false);
          seenKeys.add(key);

          const cond = leaf[key] as Record<string, unknown>;
          const ops = Object.keys(cond);
          expect(ops.length).toBe(1);
          const op = ops[0];
          expect(["$eq", "$in"]).toContain(op);
          const raw = cond[op];

          if (key === "recordType") {
            expect(recordType).not.toBe("");
            expect(op).toBe("$eq");
            expect(raw).toBe(recordType);
            continue;
          }

          expect(key.startsWith("customMetadata.")).toBe(true);
          const field = byName.get(key.slice("customMetadata.".length));
          expect(field).toBeDefined();
          if (!field) continue;

          if (op === "$in") {
            expect(Array.isArray(raw)).toBe(true);
            expect((raw as unknown[]).length).toBeGreaterThanOrEqual(2);
          } else {
            expect(Array.isArray(raw)).toBe(false);
          }
          const vals = op === "$in" ? (raw as unknown[]) : [raw];
          for (const v of vals) {
            if (field.type === "boolean") {
              expect(typeof v).toBe("boolean");
              expect(v).toBe(selections[field.name]);
            } else {
              expect(typeof v).toBe("string");
              expect(field.enumValues).toContain(v);
            }
          }
        }
        // The record type leaf comes first when present.
        if (recordType !== "") expect(Object.keys(leaves[0] ?? {})).toEqual(["recordType"]);
      }),
      RUNS,
    );
  });

  // Feature: registry-custom-metadata, Property 8: Display formatting is safe and type-aware
  it("Property 8: display formatting is safe and type-aware", () => {
    // **Validates: Requirements 8.1, 8.2, 8.3, 8.4**

    /** A field set with optional field-level titles, or undefined (Schema_Load_Failure). */
    const displayFieldsArb: fc.Arbitrary<MetadataField[] | undefined> = fc.option(
      fieldSetArb.chain((fields) =>
        fc
          .tuple(...fields.map(() => fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined })))
          .map((titles) =>
            fields.map((f, i) => (titles[i] === undefined ? f : { ...f, title: titles[i] })),
          ),
      ),
      { nil: undefined },
    );

    /** Stored values: strings, booleans, numbers, and URLs with any scheme. */
    const storedValueArb: fc.Arbitrary<unknown> = fc.oneof(
      fc.boolean(),
      fc.string({ maxLength: 20 }),
      fc.constantFrom("true", "false", "yes"),
      fc.webUrl(),
      fc.constantFrom(
        "javascript:alert(1)",
        "data:text/html,x",
        "ftp://example.com",
        "mailto:a@b.c",
        "HTTP://EXAMPLE.COM",
        "//example.com",
        " https://example.com",
      ),
      fc.integer(),
    );

    const caseArb = displayFieldsArb.chain((fields) => {
      const list = fields ?? [];
      const names = new Set(list.map((f) => f.name));
      return fc
        .tuple(
          fc.tuple(...list.map(() => fc.option(storedValueArb, { nil: undefined }))),
          fc.uniqueArray(fieldNameArb.filter((n) => !names.has(n)), { maxLength: 4 }),
        )
        .chain(([schemaVals, extraKeys]) =>
          fc.tuple(...extraKeys.map(() => storedValueArb)).chain((extraVals) => {
            const entries: [string, unknown][] = [];
            list.forEach((f, i) => {
              if (schemaVals[i] !== undefined) entries.push([f.name, schemaVals[i]]);
            });
            extraKeys.forEach((k, i) => entries.push([k, extraVals[i]]));
            // Shuffle stored order so schema order must come from the field set.
            return fc
              .shuffledSubarray(entries, { minLength: entries.length, maxLength: entries.length })
              .map((shuffled) => ({ fields, stored: Object.fromEntries(shuffled) }));
          }),
        );
    });

    const isHttp = (v: unknown) =>
      typeof v === "string" && (v.startsWith("http://") || v.startsWith("https://"));

    fc.assert(
      fc.property(caseArb, ({ fields, stored }) => {
        const out = displayValues(fields, stored);
        const byName = new Map((fields ?? []).map((f) => [f.name, f]));

        // Expected key order: schema fields with a stored value, then extras in stored order.
        const schemaKeys = (fields ?? []).map((f) => f.name).filter((k) => Object.prototype.hasOwnProperty.call(stored, k));
        const extraKeys = Object.keys(stored).filter((k) => !byName.has(k));
        const expectedKeys = [...schemaKeys, ...extraKeys];

        // Every stored key appears exactly once, schema fields first.
        expect(out.length).toBe(Object.keys(stored).length);
        expect(out.length).toBe(expectedKeys.length);

        out.forEach((entry, i) => {
          const key = expectedKeys[i];
          const value = stored[key];
          const field = byName.get(key);
          const schemaLabel = field ? (field.title ?? field.name) : undefined;

          if (entry.kind === "link") {
            // Req 8.3: links only from URL fields with an http(s) string.
            expect(field?.type).toBe("url");
            expect(isHttp(value)).toBe(true);
            expect(entry.href).toBe(value);
            expect(entry.label).toBe(schemaLabel);
            return;
          }

          if (field?.type === "url" && isHttp(value)) {
            throw new Error(`expected a link for ${key}`);
          }
          if (field?.type === "boolean" && typeof value === "boolean") {
            // Req 8.2: Yes / No, labeled from the schema (Req 8.1).
            expect(entry.text).toBe(value ? "Yes" : "No");
            expect(entry.label).toBe(schemaLabel);
          } else if (
            (field?.type === "text" || field?.type === "enum") &&
            typeof value === "string"
          ) {
            // Req 8.1: matching value, schema title or else field name.
            expect(entry.text).toBe(value);
            expect(entry.label).toBe(schemaLabel);
          } else {
            // Req 8.4: mismatch, non-http(s) URL, extra key or no schema → raw text by name.
            expect(entry.text).toBe(String(value));
            expect(entry.label).toBe(key);
          }
        });
      }),
      RUNS,
    );
  });
});
