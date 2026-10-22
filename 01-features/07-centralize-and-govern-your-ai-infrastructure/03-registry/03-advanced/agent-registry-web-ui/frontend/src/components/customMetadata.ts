// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * Pure logic for Agent Registry custom metadata: schema parsing (this file
 * section), and later value validation, payload building, search filters and
 * display formatting. No React here, so everything is unit-testable.
 *
 * A registry's `customMetadataSchemaConfiguration` holds a `defaultSchema`
 * (common attributes for every record type) and optional per-record-type
 * overrides. Each schema is a JSON *string* of the form
 * `{"type":"object","properties":{...},"required":[...]}`. A field is one of:
 *   - text    {"type":"string"}
 *   - enum    {"type":"string","enum":[...]}
 *   - url     {"type":"string","format":"uri"}
 *   - boolean {"type":"boolean"}
 * Fields may additionally carry `title` and `description`; nothing else.
 */
import type {
  CustomMetadataSchemaConfiguration,
  UpdatedCustomMetadataMap,
} from "@aws-sdk/client-agent-registry-control";

export type FieldType = "text" | "enum" | "url" | "boolean";
export type MetadataValue = string | boolean;
export type CustomMetadata = Record<string, MetadataValue>;

export interface MetadataField {
  name: string;
  type: FieldType;
  required: boolean;
  title?: string;
  description?: string;
  /** Enum fields only. */
  enumValues?: string[];
  /** Common_Attribute (from `defaultSchema`) vs Record_Type_Field (override only). */
  source: "common" | "recordType";
}

export type ParseResult =
  | { ok: true; fields: MetadataField[] }
  /** The message names the schema and, where applicable, the field. */
  | { ok: false; error: string };

export interface MetadataError {
  field: string;
  message: string;
}

/** Record types that always get a schema tab, in display order. */
const BASE_RECORD_TYPES = ["MCP", "AGENT", "SKILL", "CUSTOM"];

/** Keys a field definition may carry besides its type keys. */
const FIELD_EXTRA_KEYS = new Set(["title", "description"]);

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

type FieldParse =
  | { ok: true; field: Omit<MetadataField, "source"> }
  | { ok: false; error: string };

/** Classify one property definition into a Field_Type. */
function parseField(
  label: string,
  name: string,
  def: unknown,
  required: boolean,
): FieldParse {
  const unsupported: FieldParse = {
    ok: false,
    error: `${label}: field "${name}" is not a supported field type`,
  };
  if (!isPlainObject(def)) return unsupported;

  const typeKeys = new Set(["type"]);
  let type: FieldType;
  let enumValues: string[] | undefined;
  if (def.type === "boolean") {
    type = "boolean";
  } else if (def.type === "string") {
    const hasEnum = "enum" in def;
    const hasFormat = "format" in def;
    if (hasEnum && hasFormat) return unsupported;
    if (hasEnum) {
      const values = def.enum;
      if (
        !Array.isArray(values) ||
        values.length === 0 ||
        !values.every((x) => typeof x === "string")
      ) {
        return unsupported;
      }
      type = "enum";
      enumValues = Array.from(new Set(values as string[]));
      typeKeys.add("enum");
    } else if (hasFormat) {
      if (def.format !== "uri") return unsupported;
      type = "url";
      typeKeys.add("format");
    } else {
      type = "text";
    }
  } else {
    return unsupported;
  }

  for (const key of Object.keys(def)) {
    if (!typeKeys.has(key) && !FIELD_EXTRA_KEYS.has(key)) return unsupported;
  }
  if (def.title !== undefined && typeof def.title !== "string") return unsupported;
  if (def.description !== undefined && typeof def.description !== "string") {
    return unsupported;
  }

  const field: Omit<MetadataField, "source"> = { name, type, required };
  if (typeof def.title === "string") field.title = def.title;
  if (typeof def.description === "string") field.description = def.description;
  if (enumValues) field.enumValues = enumValues;
  return { ok: true, field };
}

type SchemaParse =
  | { ok: true; fields: Omit<MetadataField, "source">[] }
  | { ok: false; error: string };

/** Parse one schema JSON string into its fields, in declaration order. */
function parseOneSchema(label: string, raw: string): SchemaParse {
  let doc: unknown;
  try {
    doc = JSON.parse(raw);
  } catch {
    return { ok: false, error: `${label}: not valid JSON` };
  }
  if (!isPlainObject(doc) || doc.type !== "object") {
    return { ok: false, error: `${label}: not an object schema` };
  }
  const props = doc.properties ?? {};
  if (!isPlainObject(props)) {
    return { ok: false, error: `${label}: "properties" is not an object` };
  }
  const req = doc.required ?? [];
  if (!Array.isArray(req) || !req.every((x) => typeof x === "string")) {
    return { ok: false, error: `${label}: "required" is not a list of field names` };
  }
  const requiredSet = new Set(req as string[]);

  const fields: Omit<MetadataField, "source">[] = [];
  for (const [name, def] of Object.entries(props)) {
    const r = parseField(label, name, def, requiredSet.has(name));
    if (!r.ok) return r;
    fields.push(r.field);
  }
  return { ok: true, fields };
}

/**
 * Req 4. The Effective_Field_Set for one record type: common attributes first
 * in declaration order, then override-only fields. A field repeated in the
 * override keeps its common position and source, takes the union of enum
 * values (common order first) and is required if either schema requires it.
 */
export function parseSchema(
  config: CustomMetadataSchemaConfiguration | undefined,
  recordType: string,
): ParseResult {
  const fields: MetadataField[] = [];
  if (!config) return { ok: true, fields };

  if (config.defaultSchema !== undefined) {
    const common = parseOneSchema("default schema", config.defaultSchema);
    if (!common.ok) return common;
    for (const f of common.fields) fields.push({ ...f, source: "common" });
  }

  const override = config.recordTypeSchemaOverrides?.find(
    (o) => o.recordType === recordType,
  );
  if (override?.schema !== undefined) {
    const own = parseOneSchema(`${recordType} schema`, override.schema);
    if (!own.ok) return own;
    for (const f of own.fields) {
      const idx = fields.findIndex((x) => x.name === f.name);
      if (idx === -1) {
        fields.push({ ...f, source: "recordType" });
        continue;
      }
      const base = fields[idx];
      if (base.type !== f.type) {
        // The service rejects this; treat it as a parse error rather than guess.
        return {
          ok: false,
          error: `${recordType} schema: field "${f.name}" has a different type than in the default schema`,
        };
      }
      const merged: MetadataField = {
        ...base,
        required: base.required || f.required,
      };
      if (base.enumValues || f.enumValues) {
        merged.enumValues = Array.from(
          new Set([...(base.enumValues ?? []), ...(f.enumValues ?? [])]),
        );
      }
      fields[idx] = merged;
    }
  }
  return { ok: true, fields };
}

/** Req 9.1. MCP, AGENT, SKILL, CUSTOM plus any other type named in an override. */
export function schemaRecordTypes(config: CustomMetadataSchemaConfiguration): string[] {
  const types = [...BASE_RECORD_TYPES];
  for (const o of config.recordTypeSchemaOverrides ?? []) {
    if (o.recordType && !types.includes(o.recordType)) types.push(o.recordType);
  }
  return types;
}

/** Glossary Has_Value: a native boolean, or a string non-empty after trimming. */
export function hasValue(v: unknown): boolean {
  return typeof v === "boolean" || (typeof v === "string" && v.trim() !== "");
}

/** Req 5.2. Service limit on Text and URL values, counted after trimming. */
export const MAX_TEXT_LENGTH = 128;

/** Req 5.3. Absolute URI: a scheme, a colon, then at least one character. */
const ABSOLUTE_URI = /^[A-Za-z][A-Za-z0-9+.-]*:.+$/;

const TYPE_NAMES: Record<FieldType, string> = {
  text: "a text value",
  url: "a URL string",
  enum: "one of the allowed values (a string)",
  boolean: "true or false (a boolean)",
};

/**
 * Req 5.1-5.8. Every error in one pass; `[]` when nothing Has_Value.
 *
 * Decisions:
 * - `undefined` and `null` mean "not set" and are skipped, like an empty or
 *   whitespace-only string.
 * - Text and URL values are checked after trimming (length and URL syntax),
 *   matching `toCustomMetadata`, which trims before sending.
 * - Enum values must match exactly, without trimming (they come from a dropdown).
 * - A value of the wrong type gets only the type error, and a required field
 *   with a wrong-typed value is not also reported as missing.
 * - Unknown keys are reported whenever they are present (not undefined/null).
 */
export function validateMetadata(
  fields: MetadataField[],
  values: Record<string, unknown>,
): MetadataError[] {
  if (!Object.values(values).some(hasValue)) return [];

  const errors: MetadataError[] = [];
  const byName = new Map(fields.map((f) => [f.name, f]));

  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null) continue;
    if (!byName.has(key)) {
      errors.push({
        field: key,
        message: `"${key}" is unknown for the selected record type`,
      });
    }
  }

  for (const field of fields) {
    const value = values[field.name];
    const label = field.title ?? field.name;
    if (value === undefined || value === null) {
      if (field.required) {
        errors.push({ field: field.name, message: `${label} is required` });
      }
      continue;
    }

    const typeOk =
      field.type === "boolean" ? typeof value === "boolean" : typeof value === "string";
    if (!typeOk) {
      errors.push({
        field: field.name,
        message: `${label} must be ${TYPE_NAMES[field.type]}`,
      });
      continue;
    }

    if (!hasValue(value)) {
      if (field.required) {
        errors.push({ field: field.name, message: `${label} is required` });
      }
      continue;
    }
    if (typeof value !== "string") continue; // boolean: nothing more to check

    if (field.type === "enum") {
      const allowed = field.enumValues ?? [];
      if (!allowed.includes(value)) {
        errors.push({
          field: field.name,
          message: `${label} must be one of: ${allowed.join(", ")}`,
        });
      }
      continue;
    }

    const trimmed = value.trim();
    if (trimmed.length > MAX_TEXT_LENGTH) {
      errors.push({
        field: field.name,
        message: `${label} must be at most ${MAX_TEXT_LENGTH} characters`,
      });
    }
    if (field.type === "url" && !ABSOLUTE_URI.test(trimmed)) {
      errors.push({
        field: field.name,
        message: `${label} is not a valid URL (expected an absolute URI such as https://example.com)`,
      });
    }
  }
  return errors;
}

/**
 * Req 6.5, 7.2. The payload: fields of the set whose value Has_Value, Text and
 * URL trimmed, booleans kept native. Never undefined (empty object when none).
 */
export function toCustomMetadata(
  fields: MetadataField[],
  form: Record<string, MetadataValue | undefined>,
): CustomMetadata {
  const out: CustomMetadata = {};
  for (const field of fields) {
    const value = form[field.name];
    if (!hasValue(value)) continue;
    if (typeof value === "boolean") {
      out[field.name] = value;
    } else if (typeof value === "string") {
      out[field.name] =
        field.type === "text" || field.type === "url" ? value.trim() : value;
    }
  }
  return out;
}

/** Req 6.4, 7.1. Exactly the stored entries whose key is in the field set. */
export function pickFields(
  fields: MetadataField[],
  values: Record<string, unknown>,
): Record<string, MetadataValue> {
  const names = new Set(fields.map((f) => f.name));
  const out: Record<string, MetadataValue> = {};
  for (const [key, value] of Object.entries(values)) {
    if (!names.has(key)) continue;
    if (typeof value === "string" || typeof value === "boolean") out[key] = value;
  }
  return out;
}

/** Same key set and `===` per key, so `"true"` and `true` differ. */
function metadataEqual(a: CustomMetadata, b: CustomMetadata): boolean {
  const aKeys = Object.keys(a);
  if (aKeys.length !== Object.keys(b).length) return false;
  return aKeys.every((k) => Object.prototype.hasOwnProperty.call(b, k) && a[k] === b[k]);
}

/**
 * Req 7.3-7.6. undefined = omit the member. The service replaces all values,
 * so a change sends the whole edited map; clearing everything sends `{}`.
 * An empty edit against an empty or absent original counts as equal.
 */
export function buildMetadataUpdate(
  original: CustomMetadata | undefined,
  edited: CustomMetadata,
): UpdatedCustomMetadataMap | undefined {
  if (metadataEqual(original ?? {}, edited)) return undefined;
  if (Object.keys(edited).length === 0) return { optionalValue: {} };
  return { optionalValue: edited };
}

/** Enum or Boolean, the only Field_Types a search filter can constrain. */
function isFilterable(f: MetadataField): boolean {
  return f.type === "enum" || f.type === "boolean";
}

/** Common attributes only: parse the default schema alone, ignoring overrides. */
function commonFields(
  config: CustomMetadataSchemaConfiguration | undefined,
): MetadataField[] | undefined {
  if (!config) return [];
  const parsed = parseSchema({ defaultSchema: config.defaultSchema }, "");
  return parsed.ok ? parsed.fields : undefined;
}

/**
 * Req 10.1, 10.11. Enum and Boolean fields offered as filters: common
 * attributes when `recordType` is "" (all types), otherwise that type's
 * Effective_Field_Set. `[]` on a parse error or absent config.
 */
export function filterableFields(
  config: CustomMetadataSchemaConfiguration | undefined,
  recordType: string,
): MetadataField[] {
  if (!config) return [];
  if (recordType === "") return (commonFields(config) ?? []).filter(isFilterable);
  const parsed = parseSchema(config, recordType);
  return parsed.ok ? parsed.fields.filter(isFilterable) : [];
}

/** Field name to selected enum values or a boolean; an absent key means "any". */
export type FilterSelections = Record<string, string[] | boolean>;

/**
 * Req 10.2, 10.4-10.7, 10.12. Conditions in order: the record type (if any),
 * then one per constrained field in `fields` order. Disallowed enum values,
 * unknown keys and selections of the wrong kind are dropped. undefined when
 * there are no conditions.
 */
export function buildSearchFilter(
  recordType: string,
  fields: MetadataField[],
  selections: FilterSelections,
): Record<string, unknown> | undefined {
  const conditions: Record<string, unknown>[] = [];
  if (recordType !== "") conditions.push({ recordType: { $eq: recordType } });

  for (const field of fields) {
    if (!Object.prototype.hasOwnProperty.call(selections, field.name)) continue;
    const sel = selections[field.name];
    const key = `customMetadata.${field.name}`;
    if (field.type === "boolean") {
      if (typeof sel === "boolean") conditions.push({ [key]: { $eq: sel } });
      continue;
    }
    if (field.type !== "enum" || !Array.isArray(sel)) continue;
    const allowed = field.enumValues ?? [];
    const values = Array.from(new Set(sel.filter((v) => allowed.includes(v))));
    if (values.length === 1) conditions.push({ [key]: { $eq: values[0] } });
    else if (values.length > 1) conditions.push({ [key]: { $in: values } });
  }

  if (conditions.length === 0) return undefined;
  if (conditions.length === 1) return conditions[0];
  return { $and: conditions };
}

/**
 * Req 11.1, 11.2. The first Enum or Boolean common attribute as one `$eq`
 * condition (first enum value, or native `true`). undefined when there is
 * none or the default schema does not parse.
 */
export function exampleFilter(
  config: CustomMetadataSchemaConfiguration | undefined,
): Record<string, unknown> | undefined {
  const field = (commonFields(config) ?? []).find(isFilterable);
  if (!field) return undefined;
  const key = `customMetadata.${field.name}`;
  if (field.type === "boolean") return { [key]: { $eq: true } };
  const first = field.enumValues?.[0];
  return first === undefined ? undefined : { [key]: { $eq: first } };
}
export type DisplayValue =
  | { kind: "text"; label: string; text: string }
  | { kind: "link"; label: string; href: string };
/**
 * Raw fallback text (Req 8.4). Strings and other primitives use `String(value)`;
 * `null` shows as "null"; objects and arrays use `JSON.stringify` so they do
 * not collapse to "[object Object]" (falls back to `String` if that throws).
 */
function rawText(value: unknown): string {
  if (value !== null && typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
function isHttpUrl(v: string): boolean {
  return v.startsWith("http://") || v.startsWith("https://");
}
function displayOne(field: MetadataField | undefined, key: string, value: unknown): DisplayValue {
  if (field) {
    const label = field.title ?? field.name;
    if (field.type === "boolean" && typeof value === "boolean") {
      return { kind: "text", label, text: value ? "Yes" : "No" };
    }
    if (field.type === "url" && typeof value === "string" && isHttpUrl(value)) {
      return { kind: "link", label, href: value };
    }
    if ((field.type === "text" || field.type === "enum") && typeof value === "string") {
      return { kind: "text", label, text: value };
    }
  }
  return { kind: "text", label: key, text: rawText(value) };
}
/**
 * Req 6.7, 8.1-8.4, 8.7. Schema fields first (in schema order, only those with
 * a stored value), then remaining stored keys in stored order. Every stored key
 * (value not undefined) appears exactly once. Pass `fields` undefined on a
 * Schema_Load_Failure to show everything as raw text labeled by key.
 */
export function displayValues(
  fields: MetadataField[] | undefined,
  values: Record<string, unknown> | undefined,
): DisplayValue[] {
  if (!values) return [];
  const present = (k: string) => Object.prototype.hasOwnProperty.call(values, k) && values[k] !== undefined;
  const out: DisplayValue[] = [];
  const seen = new Set<string>();
  for (const field of fields ?? []) {
    if (seen.has(field.name) || !present(field.name)) continue;
    seen.add(field.name);
    out.push(displayOne(field, field.name, values[field.name]));
  }
  for (const key of Object.keys(values)) {
    if (seen.has(key) || !present(key)) continue;
    seen.add(key);
    out.push(displayOne(undefined, key, values[key]));
  }
  return out;
}
/**
 * Req 8.5. `type` is a Cloudscape `StatusIndicator` type ("success" / "warning")
 * or "none", meaning render the plain dash label instead of an indicator.
 */
export function complianceLabel(
  status: string | undefined,
): { type: "success" | "warning" | "none"; label: string } {
  if (status === "COMPLIANT") return { type: "success", label: "Compliant" };
  if (status === "NON_COMPLIANT") return { type: "warning", label: "Non-compliant" };
  return { type: "none", label: "-" };
}
