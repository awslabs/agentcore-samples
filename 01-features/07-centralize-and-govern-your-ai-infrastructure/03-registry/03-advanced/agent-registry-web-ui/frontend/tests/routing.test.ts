// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

// The router is the app's navigation contract. Rather than mount React, we pin
// the literal set of route `path=` values declared in App.tsx so a repackage
// that touches routing is caught. If App.tsx moves, update the path below.
const here = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(here, "../src/App.tsx"), "utf8");

describe("router contract", () => {
  it("declares exactly the expected route paths", () => {
    const paths = [...appSource.matchAll(/path="([^"]+)"/g)]
      .map((m) => m[1])
      .sort();
    expect(paths).toEqual(
      [
        "/*",
        "/",
        "/login",
        "/registries/:registryId",
        "/registries/:registryId/records/new",
        "/registries/:registryId/records/:recordId/edit",
        "/registries/:registryId/records/:recordId",
        "*",
      ].sort(),
    );
  });
});
