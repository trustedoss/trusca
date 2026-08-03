// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * About — what this deployment is, and the license notices it ships with.
 *
 * Why the notices are in the product and not just a link to GitHub: TRUSCA is
 * self-hosted and supports air-gapped installs, where an outbound link is not an
 * answer. The files are in every image at `/licenses/`, which serves the operator
 * with shell access; this page serves everyone else.
 *
 * The document bodies render as preformatted text, never markdown. A license text
 * that has been reflowed or prettified is no longer the notice it stands in for.
 *
 * Document titles and descriptions are translated by id here rather than shown
 * from the API response: the backend's strings are English (they are also the
 * OpenAPI contract), and a Korean reader should not get an English tab label. The
 * API values remain the fallback for a document this UI does not know yet.
 */
import { ExternalLink, FileText } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/PageHeader";
import { EmptyState } from "@/components/EmptyState";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAbout, useNotice } from "@/features/about/api/useAbout";
import type { NoticeDocument } from "@/features/about/api/aboutApi";

/** Translate a document's title/description by id, falling back to the API. */
function useDocumentLabels(doc: NoticeDocument) {
  const { t } = useTranslation("about");
  const key = doc.id.replace(/-/g, "_");
  return {
    title: t(`documents.${key}.title`, { defaultValue: doc.title }),
    description: t(`documents.${key}.description`, {
      defaultValue: doc.description,
    }),
  };
}

function NoticeBody({ documentId }: { documentId: string }) {
  const { t } = useTranslation("about");
  const { data, isLoading, isError, error } = useNotice(documentId);

  if (isLoading) {
    return (
      <div className="space-y-2" data-testid="about-notice-loading">
        {Array.from({ length: 8 }).map((_, index) => (
          <Skeleton key={index} className="h-4 w-full" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <Alert variant="destructive" data-testid="about-notice-error">
        <AlertDescription>
          {t("notice.load_failed", { detail: error?.message ?? "" })}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    // `overflow-x-auto` on the wrapper, not the page: the Apache-2.0 text has
    // long lines and the page body must never scroll sideways.
    <div className="max-h-[32rem] overflow-auto rounded-md border bg-muted/30 p-4">
      <pre
        className="font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-foreground"
        data-testid="about-notice-body"
      >
        {data}
      </pre>
    </div>
  );
}

function MissingDocument({ filename }: { filename: string }) {
  const { t } = useTranslation("about");
  return (
    <EmptyState
      icon={<FileText />}
      title={t("notice.missing_title")}
      description={t("notice.missing_description", { filename })}
      data-testid="about-notice-missing"
    />
  );
}

export default function AboutPage() {
  const { t } = useTranslation("about");
  const { data, isLoading, isError, error } = useAbout();
  const [activeTab, setActiveTab] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} description={t("subtitle")} />
        <Skeleton className="h-32 w-full" data-testid="about-loading" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="space-y-6">
        <PageHeader title={t("title")} description={t("subtitle")} />
        <Alert variant="destructive" data-testid="about-error">
          <AlertDescription>
            {t("load_failed", { detail: error?.message ?? "" })}
          </AlertDescription>
        </Alert>
      </div>
    );
  }

  const documents = data.documents;
  const selected = activeTab ?? documents[0]?.id ?? "";

  return (
    <div className="space-y-6" data-testid="about-page">
      <PageHeader title={t("title")} description={t("subtitle")} />

      <Card className="p-6">
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="text-xs font-medium text-muted-foreground">
              {t("field.product")}
            </dt>
            <dd className="mt-1 text-sm font-semibold" data-testid="about-product">
              {data.product}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">
              {t("field.version")}
            </dt>
            <dd className="mt-1 font-mono text-sm" data-testid="about-version">
              {data.version}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">
              {t("field.license")}
            </dt>
            <dd className="mt-1 flex items-center gap-2">
              <Badge variant="outline" data-testid="about-license">
                {data.license_spdx_id}
              </Badge>
              <a
                href={data.license_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 text-sm text-brand hover:underline"
              >
                {data.license_name}
                <ExternalLink className="size-3" aria-hidden="true" />
              </a>
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-muted-foreground">
              {t("field.copyright")}
            </dt>
            <dd className="mt-1 text-sm" data-testid="about-copyright">
              {data.copyright}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium text-muted-foreground">
              {t("field.source")}
            </dt>
            <dd className="mt-1">
              <a
                href={data.source_url}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center gap-1 font-mono text-sm text-brand hover:underline"
                data-testid="about-source-link"
              >
                {data.source_url}
                <ExternalLink className="size-3" aria-hidden="true" />
              </a>
            </dd>
          </div>
        </dl>
      </Card>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold tracking-tight">
          {t("notices.heading")}
        </h2>
        <p className="text-sm text-muted-foreground">{t("notices.intro")}</p>

        {documents.length === 0 ? (
          <EmptyState
            icon={<FileText />}
            title={t("notices.none_title")}
            description={t("notices.none_description")}
          />
        ) : (
          <Tabs value={selected} onValueChange={setActiveTab}>
            <TabsList data-testid="about-notice-tabs">
              {documents.map((doc) => (
                <NoticeTab key={doc.id} doc={doc} />
              ))}
            </TabsList>
            {documents.map((doc) => (
              <NoticePanel key={doc.id} doc={doc} />
            ))}
          </Tabs>
        )}
      </section>
    </div>
  );
}

function NoticeTab({ doc }: { doc: NoticeDocument }) {
  const { title } = useDocumentLabels(doc);
  return (
    <TabsTrigger value={doc.id} data-testid={`about-tab-${doc.id}`}>
      {title}
    </TabsTrigger>
  );
}

function NoticePanel({ doc }: { doc: NoticeDocument }) {
  const { description } = useDocumentLabels(doc);
  return (
    <TabsContent value={doc.id} className="space-y-3">
      <p className="text-sm text-muted-foreground">{description}</p>
      {doc.size_bytes === null ? (
        <MissingDocument filename={doc.filename} />
      ) : (
        <NoticeBody documentId={doc.id} />
      )}
    </TabsContent>
  );
}
