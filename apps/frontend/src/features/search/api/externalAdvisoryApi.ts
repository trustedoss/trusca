// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * "What is this vulnerability" is a different question from "are we
 * affected" -- the latter is already answered by the existing
 * `kind=vulnerabilities` search results (internal scan matches). This is
 * purely the external advisory description, shown as a card above those
 * results when the typed term looks like a CVE or GHSA id.
 */
import { api } from "@/lib/api";

export interface ExternalAdvisoryOut {
  advisory_id: string;
  found: boolean;
  title: string | null;
  cvss3_score: number | null;
  cvss3_vector: string | null;
  aliases: string[];
}

export async function lookupExternalAdvisory(advisoryId: string): Promise<ExternalAdvisoryOut> {
  const { data } = await api.get<ExternalAdvisoryOut>(
    `/v1/external-advisories/${encodeURIComponent(advisoryId)}`,
  );
  return data;
}
