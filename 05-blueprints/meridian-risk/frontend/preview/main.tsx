// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import PreviewApp from "./PreviewApp"
import { installMockApi } from "./mockApi"
import "../src/styles.css"
import "./palettes.css"

// Must run before React mounts, so the app's first /config.json fetch is served
// by the mock and no AWS call is ever attempted.
installMockApi()

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <PreviewApp />
  </StrictMode>
)
