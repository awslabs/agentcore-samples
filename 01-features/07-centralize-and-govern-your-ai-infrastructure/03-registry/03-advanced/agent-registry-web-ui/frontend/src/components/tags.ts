// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Client-side tag validation mirroring the Agent Registry `TagResource` /
 * `CreateRegistryRecord` constraints, so an invalid tag is caught inline in the
 * wizard instead of failing at the API call with a raw ValidationException.
 *
 * Constraints (from the agent-registry-control model):
 *   - at most 50 tags per resource
 *   - key:   1-128 chars, pattern [a-zA-Z0-9\s._:/=+@-]*
 *   - value: 0-256 chars, same pattern (an empty value is legal)
 *   - keys must be unique within a request
 *   - the `aws:` prefix is reserved by AWS and cannot be used
 */

export const MAX_TAGS = 50;
export const MAX_KEY_LENGTH = 128;
export const MAX_VALUE_LENGTH = 256;

/** Allowed characters for both keys and values. */
const TAG_PATTERN = /^[a-zA-Z0-9\s._:/=+@-]*$/;

export interface TagPair {
  key: string;
  value: string;
}

/** Validate one key. Returns an error string, or undefined when valid. */
export function validateTagKey(
  key: string,
  allKeys: string[] = [],
): string | undefined {
  if (!key) return "Key is required.";
  if (key.length > MAX_KEY_LENGTH)
    return `Key must be ${MAX_KEY_LENGTH} characters or fewer.`;
  if (!TAG_PATTERN.test(key)) {
    return "Only letters, digits, spaces, and . _ : / = + - @ are allowed.";
  }
  if (key.toLowerCase().startsWith("aws:"))
    return "The 'aws:' prefix is reserved by AWS.";
  if (allKeys.filter((k) => k === key).length > 1)
    return "Keys must be unique.";
  return undefined;
}

/** Validate one value. Returns an error string, or undefined when valid. */
export function validateTagValue(value: string): string | undefined {
  if (value.length > MAX_VALUE_LENGTH) {
    return `Value must be ${MAX_VALUE_LENGTH} characters or fewer.`;
  }
  if (!TAG_PATTERN.test(value)) {
    return "Only letters, digits, spaces, and . _ : / = + - @ are allowed.";
  }
  return undefined;
}

/**
 * Whole-collection validation. Returns every problem found, so the wizard can
 * block Next with a complete message rather than one error at a time.
 */
export function validateTags(pairs: TagPair[]): string[] {
  const errors: string[] = [];
  const nonEmpty = pairs.filter((p) => p.key !== "" || p.value !== "");
  if (nonEmpty.length > MAX_TAGS) {
    errors.push(
      `At most ${MAX_TAGS} tags are allowed (${nonEmpty.length} provided).`,
    );
  }
  const keys = nonEmpty.map((p) => p.key);
  nonEmpty.forEach((p) => {
    const label = `Tag "${p.key || "(empty key)"}"`;
    // Key and value share the same character rule, so the messages must name which
    // side failed — otherwise they dedupe into one and the user cannot tell.
    const keyError = validateTagKey(p.key, keys);
    if (keyError) errors.push(`${label} key: ${keyError}`);
    const valueError = validateTagValue(p.value);
    if (valueError) errors.push(`${label} value: ${valueError}`);
  });
  return [...new Set(errors)];
}

/**
 * Convert editor rows to the API's tag map, dropping fully-empty rows.
 * Returns undefined when there is nothing to send, so the caller can omit the
 * field entirely rather than sending an empty map.
 */
export function toTagMap(pairs: TagPair[]): Record<string, string> | undefined {
  const entries = pairs
    .filter((p) => p.key !== "")
    .map((p) => [p.key, p.value] as const);
  if (entries.length === 0) return undefined;
  return Object.fromEntries(entries);
}

/** Convert an API tag map back to editor rows (stable, key-sorted). */
export function toTagPairs(
  tags: Record<string, string> | undefined,
): TagPair[] {
  if (!tags) return [];
  return Object.keys(tags)
    .sort()
    .map((key) => ({ key, value: tags[key] ?? "" }));
}
