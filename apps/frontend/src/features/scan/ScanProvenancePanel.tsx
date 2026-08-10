// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * ScanProvenancePanel — what a scan read, and what it was handed (gap #31).
 *
 * The panel a reader reaches for after asking "why isn't this component in the
 * results?". A source scan shows the dependency manifests its tree carried; an
 * ingest shows what the uploaded document claimed about itself. Both are
 * recorded at scan time, so the answer survives the preserved tarball being
 * reclaimed — which is when the question is usually asked.
 *
 * Nothing recorded renders nothing. A scan from before the feature, or one
 * whose tree carried no manifests, has no story to tell here and an empty card
 * saying so would be noise on every older scan.
 *
 * Every value is what the source said, not something we verified: a document's
 * timestamp is the generator's claim, and a manifest's presence means the file
 * was in the tree, not that the scanner understood it.
 */
import { FileSearch } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import type { ScanProvenanceRead } from "@/lib/projectsApi";

export interface ScanProvenancePanelProps {
  provenance: ScanProvenanceRead | undefined;
}

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function ScanProvenancePanel({ provenance }: ScanProvenancePanelProps) {
  const { t } = useTranslation("scans");

  const manifests = provenance?.manifests ?? null;
  const document = provenance?.document ?? null;
  if (!manifests && !document) {
    return null;
  }

  return (
    <section
      className="rounded-lg border border-border bg-card p-4"
      aria-labelledby="scan-provenance-heading"
    >
      <div className="mb-3 flex items-center gap-2">
        <FileSearch className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h2
          id="scan-provenance-heading"
          className="text-sm font-semibold tracking-tight"
        >
          {t("provenance.title")}
        </h2>
      </div>
      <p className="mb-4 text-xs text-muted-foreground">
        {t("provenance.description")}
      </p>

      {document ? <DocumentSummary document={document} /> : null}
      {manifests ? <ManifestList manifests={manifests} /> : null}
    </section>
  );
}

function DocumentSummary({
  document,
}: {
  document: NonNullable<ScanProvenanceRead["document"]>;
}) {
  const { t } = useTranslation("scans");
  const rows: { label: string; value: string }[] = [];

  if (document.original_filename) {
    rows.push({
      label: t("provenance.document.filename"),
      value: document.original_filename,
    });
  }
  rows.push({
    label: t("provenance.document.format"),
    value: document.spec_version
      ? `${document.format} ${document.spec_version}`
      : document.format,
  });
  if (document.subject) {
    rows.push({
      label: t("provenance.document.subject"),
      value: document.subject_version
        ? `${document.subject} ${document.subject_version}`
        : document.subject,
    });
  }
  if (document.tools.length > 0) {
    rows.push({
      label: t("provenance.document.tools"),
      value: document.tools
        .map((tool) => (tool.version ? `${tool.name} ${tool.version}` : tool.name))
        .join(", "),
    });
  }
  if (document.supplier) {
    rows.push({
      label: t("provenance.document.supplier"),
      value: document.supplier,
    });
  }
  if (document.authors.length > 0) {
    rows.push({
      label: t("provenance.document.authors"),
      value: document.authors.join(", "),
    });
  }
  if (document.created) {
    rows.push({
      label: t("provenance.document.created"),
      value: document.created,
    });
  }
  rows.push({
    label: t("provenance.document.components"),
    value: String(document.component_count),
  });
  if (document.byte_size != null) {
    rows.push({
      label: t("provenance.document.size"),
      value: formatBytes(document.byte_size),
    });
  }

  return (
    <div className="mb-4">
      <h3 className="mb-2 text-xs font-medium text-muted-foreground">
        {t("provenance.document.heading")}
      </h3>
      {/* A definition list, not a table: these are one subject's attributes
          rather than rows to compare against each other. */}
      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs">
        {rows.map((row) => (
          <div key={row.label} className="contents">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="font-mono break-all">{row.value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">
        {t("provenance.document.claimNote")}
      </p>
    </div>
  );
}

function ManifestList({
  manifests,
}: {
  manifests: NonNullable<ScanProvenanceRead["manifests"]>;
}) {
  const { t } = useTranslation("scans");

  return (
    <div>
      <h3 className="mb-2 text-xs font-medium text-muted-foreground">
        {t("provenance.manifests.heading", { count: manifests.count })}
      </h3>
      {manifests.count === 0 ? (
        <p className="text-xs text-muted-foreground">
          {t("provenance.manifests.none")}
        </p>
      ) : (
        <ul className="space-y-1">
          {manifests.files.map((file) => (
            <li
              key={file.path}
              className="flex items-baseline justify-between gap-4 text-xs"
            >
              <span className="font-mono break-all">{file.path}</span>
              <span className="shrink-0 text-muted-foreground">
                {formatBytes(file.size)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {manifests.truncated ? (
        <Badge variant="outline" className="mt-2">
          {t("provenance.manifests.truncated")}
        </Badge>
      ) : null}
    </div>
  );
}
