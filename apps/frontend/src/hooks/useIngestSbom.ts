// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * useIngestSbom — feat/demo-sandbox-scan.
 *
 * Mutation wrapper for the external SBOM ingest endpoint
 * (`POST /v1/projects/{id}/sbom-ingest`). It is the "upload an SBOM instead of
 * scanning source" lane the sandbox demo points visitors at for projects larger
 * than the live-scan cap, but it is a first-party portal feature independent of
 * demo mode.
 *
 * On success the backend returns a queued `ScanPublic` row; the caller wires it
 * into the existing `ScanProgress` drawer (the WebSocket streams matching
 * progress from there). The projects cache is invalidated so the list picks up
 * the new `latest_scan_id`. Errors are surfaced locally (the dialog renders an
 * inline alert), so the global error toast is suppressed via `meta`.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";

import {
  ingestSbom as ingestSbomApi,
  type IngestSbomOptions,
  type ScanPublic,
} from "@/lib/projectsApi";

export interface IngestSbomInput extends IngestSbomOptions {
  file: File;
}

export function useIngestSbom(projectId: string) {
  const queryClient = useQueryClient();

  return useMutation<ScanPublic, Error, IngestSbomInput>({
    mutationFn: ({ file, ref, release }) =>
      ingestSbomApi(projectId, file, { ref, release }),
    meta: { errorToast: false },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}
