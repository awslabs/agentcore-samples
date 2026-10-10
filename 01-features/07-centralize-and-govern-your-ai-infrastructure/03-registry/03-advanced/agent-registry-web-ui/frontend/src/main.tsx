// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@cloudscape-design/global-styles/index.css";
import { AuthProvider } from "./auth/AuthContext";
import { BreadcrumbsProvider } from "./components/Breadcrumbs";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <BreadcrumbsProvider>
          <App />
        </BreadcrumbsProvider>
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
