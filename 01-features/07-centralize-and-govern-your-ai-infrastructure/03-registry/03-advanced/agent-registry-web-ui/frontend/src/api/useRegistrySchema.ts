// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
import { useEffect, useState } from "react";
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
import { getRegistry, type GetRegistryResponse } from "./registry";
import { useClients } from "./useClients";

export interface RegistrySchemaState {
  /** The full GetRegistry response, or null until it loads (or if it failed). */
  registry: GetRegistryResponse | null;
  /** The registry's custom metadata schema configuration, if it has one. */
  config: CustomMetadataSchemaConfiguration | undefined;
  loading: boolean;
  /** True when GetRegistry failed. Pages treat this as a schema load failure. */
  loadError: boolean;
}

/**
 * Loads a registry (GetRegistry is allowed for every persona) and exposes its
 * custom metadata schema configuration. Results for a previous registryId, or
 * that arrive after unmount, are discarded. Pages pass `config` to
 * `parseSchema(config, recordType)`; a schema load failure is
 * `loadError || !result.ok`.
 */
export function useRegistrySchema(registryId: string): RegistrySchemaState {
  const clients = useClients();
  const [registry, setRegistry] = useState<GetRegistryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    if (!clients) return;
    let cancelled = false;
    setRegistry(null);
    setLoadError(false);
    setLoading(true);
    getRegistry(clients, registryId)
      .then((r) => {
        if (!cancelled) setRegistry(r);
      })
      .catch(() => {
        if (!cancelled) setLoadError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clients, registryId]);

  return {
    registry,
    config: registry?.customMetadataSchemaConfiguration,
    loading,
    loadError,
  };
}
