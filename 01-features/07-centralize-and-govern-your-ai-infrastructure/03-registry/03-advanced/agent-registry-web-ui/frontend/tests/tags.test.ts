// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import {
  MAX_TAGS,
  MAX_KEY_LENGTH,
  MAX_VALUE_LENGTH,
  validateTagKey,
  validateTagValue,
  validateTags,
  toTagMap,
  toTagPairs,
  type TagPair,
} from "../src/components/tags";

// Mirrors the service's tag constraints. These run before CreateRegistryRecord so
// an invalid tag surfaces inline in the wizard instead of as a raw AWS
// ValidationException after the user has filled in the whole form.

const pair = (key: string, value = ""): TagPair => ({ key, value });

describe("validateTagKey", () => {
  it("accepts a normal key", () => {
    expect(validateTagKey("owner")).toBeUndefined();
  });

  it("accepts every allowed special character", () => {
    expect(validateTagKey("a b.c_d:e/f=g+h-i@j")).toBeUndefined();
  });

  it("rejects an empty key", () => {
    expect(validateTagKey("")).toMatch(/required/i);
  });

  it(`rejects a key longer than ${MAX_KEY_LENGTH}`, () => {
    expect(validateTagKey("k".repeat(MAX_KEY_LENGTH))).toBeUndefined();
    expect(validateTagKey("k".repeat(MAX_KEY_LENGTH + 1))).toMatch(
      /128 characters or fewer/,
    );
  });

  it("rejects a disallowed character", () => {
    expect(validateTagKey("owner!")).toMatch(/allowed/i);
    expect(validateTagKey("own(er)")).toMatch(/allowed/i);
  });

  it("rejects the reserved aws: prefix, case-insensitively", () => {
    expect(validateTagKey("aws:cost")).toMatch(/reserved/i);
    expect(validateTagKey("AWS:cost")).toMatch(/reserved/i);
  });

  it("rejects a duplicate key", () => {
    expect(validateTagKey("env", ["env", "env"])).toMatch(/unique/i);
    expect(validateTagKey("env", ["env", "owner"])).toBeUndefined();
  });
});

describe("validateTagValue", () => {
  it("accepts an empty value (values are optional)", () => {
    expect(validateTagValue("")).toBeUndefined();
  });

  it(`rejects a value longer than ${MAX_VALUE_LENGTH}`, () => {
    expect(validateTagValue("v".repeat(MAX_VALUE_LENGTH))).toBeUndefined();
    expect(validateTagValue("v".repeat(MAX_VALUE_LENGTH + 1))).toMatch(
      /256 characters or fewer/,
    );
  });

  it("rejects a disallowed character", () => {
    expect(validateTagValue("prod#1")).toMatch(/allowed/i);
  });
});

describe("validateTags", () => {
  it("passes a valid collection", () => {
    expect(validateTags([pair("owner", "team"), pair("env", "prod")])).toEqual(
      [],
    );
  });

  it("ignores fully-empty rows the editor leaves behind", () => {
    expect(validateTags([pair("owner", "team"), pair("", "")])).toEqual([]);
  });

  it("flags a row with a value but no key", () => {
    expect(validateTags([pair("", "orphan")])).toHaveLength(1);
  });

  it(`caps the collection at ${MAX_TAGS} tags`, () => {
    const many = Array.from({ length: MAX_TAGS + 1 }, (_, i) =>
      pair(`k${i}`, "v"),
    );
    expect(validateTags(many).some((e) => /At most 50 tags/.test(e))).toBe(
      true,
    );
    const exact = Array.from({ length: MAX_TAGS }, (_, i) =>
      pair(`k${i}`, "v"),
    );
    expect(validateTags(exact)).toEqual([]);
  });

  it("reports duplicate keys once", () => {
    const errors = validateTags([pair("env", "a"), pair("env", "b")]);
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatch(/unique/i);
  });

  it("reports both a key and a value problem on the same row", () => {
    expect(validateTags([pair("bad!", "worse#")])).toHaveLength(2);
  });
});

describe("toTagMap / toTagPairs", () => {
  it("returns undefined for nothing to send, so the field is omitted", () => {
    expect(toTagMap([])).toBeUndefined();
    expect(toTagMap([pair("", "")])).toBeUndefined();
  });

  it("drops keyless rows and builds the API map", () => {
    expect(toTagMap([pair("owner", "team"), pair("", "orphan")])).toEqual({
      owner: "team",
    });
  });

  it("keeps a legal empty value", () => {
    expect(toTagMap([pair("owner", "")])).toEqual({ owner: "" });
  });

  it("round-trips a tag map through editor rows, key-sorted", () => {
    const tags = { zeta: "1", alpha: "2" };
    const pairs = toTagPairs(tags);
    expect(pairs.map((p) => p.key)).toEqual(["alpha", "zeta"]);
    expect(toTagMap(pairs)).toEqual(tags);
  });

  it("treats undefined tags as no rows", () => {
    expect(toTagPairs(undefined)).toEqual([]);
  });
});
