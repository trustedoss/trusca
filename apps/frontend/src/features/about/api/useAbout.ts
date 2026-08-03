// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * TanStack Query hooks for the About surface.
 *
 * Both are effectively immutable for the life of a deployment — the version and
 * the notice texts only change on upgrade — so they carry a long `staleTime` and
 * no polling. Nothing here refetches on window focus either; re-reading the
 * Apache-2.0 text because the user alt-tabbed is pure waste.
 *
 * The notice query is enabled only once a document is selected, so opening the
 * page fetches metadata alone.
 */
import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { getAbout, getNotice, type About } from "@/features/about/api/aboutApi";

/** Notices change only on upgrade — an hour is conservative, not aggressive. */
const NOTICE_STALE_MS = 60 * 60 * 1000;

export function aboutQueryKey() {
  return ["about"] as const;
}

export function noticeQueryKey(documentId: string | null) {
  return ["about", "notice", documentId] as const;
}

export function useAbout(): UseQueryResult<About, Error> {
  return useQuery({
    queryKey: aboutQueryKey(),
    queryFn: () => getAbout(),
    staleTime: NOTICE_STALE_MS,
    refetchOnWindowFocus: false,
  });
}

export function useNotice(
  documentId: string | null,
): UseQueryResult<string, Error> {
  return useQuery({
    queryKey: noticeQueryKey(documentId),
    queryFn: () => getNotice(documentId as string),
    enabled: documentId !== null,
    staleTime: NOTICE_STALE_MS,
    refetchOnWindowFocus: false,
  });
}
