// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export interface Crumb {
  text: string;
  href: string;
}

interface BreadcrumbsContextValue {
  crumbs: Crumb[];
  setCrumbs: (c: Crumb[]) => void;
}

const BreadcrumbsContext = createContext<BreadcrumbsContextValue | undefined>(
  undefined,
);

export function BreadcrumbsProvider({ children }: { children: ReactNode }) {
  const [crumbs, setCrumbs] = useState<Crumb[]>([]);
  return (
    <BreadcrumbsContext.Provider value={{ crumbs, setCrumbs }}>
      {children}
    </BreadcrumbsContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useBreadcrumbsState(): BreadcrumbsContextValue {
  const ctx = useContext(BreadcrumbsContext);
  if (!ctx)
    throw new Error(
      "useBreadcrumbsState must be used within BreadcrumbsProvider",
    );
  return ctx;
}

/** Page hook: declare the breadcrumb trail for the current page. */
// eslint-disable-next-line react-refresh/only-export-components
export function useBreadcrumbs(crumbs: Crumb[]) {
  const { setCrumbs } = useBreadcrumbsState();
  // Serialize so the effect only re-runs when the trail actually changes.
  const key = JSON.stringify(crumbs);
  useEffect(() => {
    setCrumbs(crumbs);
    return () => setCrumbs([]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}
