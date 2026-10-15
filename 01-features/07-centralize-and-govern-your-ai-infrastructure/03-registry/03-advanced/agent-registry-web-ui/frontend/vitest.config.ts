// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vitest/config";

// Behaviour-parity test suite. These tests pin the app's pure business logic
// (persona -> capability mapping, descriptor schema construction, error mapping,
// PATCH-wrapper update semantics, config assertions, and the API command shapes
// each helper sends to the SDK) so a refactor / repackaging can be proven to
// preserve behaviour: run `npm test` before and after and diff the results.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
    globals: false,
  },
});
