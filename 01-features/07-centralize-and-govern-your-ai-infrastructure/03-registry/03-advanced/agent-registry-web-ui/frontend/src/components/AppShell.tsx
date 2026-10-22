// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { type ReactNode, useState } from "react";
import AppLayout from "@cloudscape-design/components/app-layout";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import SideNavigation from "@cloudscape-design/components/side-navigation";
import BreadcrumbGroup from "@cloudscape-design/components/breadcrumb-group";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { useBreadcrumbsState } from "./Breadcrumbs";
import { config } from "../config";

export default function AppShell({ children }: { children: ReactNode }) {
  const { session, capabilities, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { crumbs } = useBreadcrumbsState();
  const [navOpen, setNavOpen] = useState(true);

  const persona = capabilities?.persona ?? "Consumer";

  return (
    <>
      <div id="top-nav">
        <TopNavigation
          identity={{
            href: "/",
            title: "AWS Agent Registry",
            onFollow: (e) => {
              e.preventDefault();
              navigate("/");
            },
          }}
          utilities={[
            {
              type: "button",
              text: `${config.region}`,
              disableTextCollapse: true,
            },
            {
              type: "menu-dropdown",
              text: session?.email ?? "user",
              description: `Role: ${persona}`,
              iconName: "user-profile",
              items: [{ id: "signout", text: "Sign out" }],
              onItemClick: ({ detail }) => {
                if (detail.id === "signout") signOut();
              },
            },
          ]}
        />
      </div>
      <AppLayout
        headerSelector="#top-nav"
        navigationOpen={navOpen}
        onNavigationChange={({ detail }) => setNavOpen(detail.open)}
        breadcrumbs={
          crumbs.length > 0 ? (
            <BreadcrumbGroup
              items={crumbs}
              onFollow={(e) => {
                e.preventDefault();
                navigate(e.detail.href);
              }}
            />
          ) : undefined
        }
        navigation={
          <SideNavigation
            activeHref={location.pathname}
            header={{ href: "/", text: "Registry" }}
            onFollow={(e) => {
              if (!e.detail.external) {
                e.preventDefault();
                navigate(e.detail.href);
              }
            }}
            items={[{ type: "link", text: "Registries", href: "/" }]}
          />
        }
        content={children}
        toolsHide
      />
    </>
  );
}
