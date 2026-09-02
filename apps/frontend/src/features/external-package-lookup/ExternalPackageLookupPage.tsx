// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 TRUSCA contributors
/**
 * Looking a package up on the external catalog before it has been scanned
 * anywhere internally.
 *
 * deps.dev only answers exact ecosystem+name lookups, not fuzzy text search,
 * so this is a submit-triggered form, not a live-typing search. The result
 * already carries internal usage (the backend cross-references it), so this
 * page never makes a second call to find out who else already uses it.
 */
import { PackageSearch } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  EXTERNAL_PACKAGE_ECOSYSTEMS,
  type ExternalPackageEcosystem,
} from "@/features/external-package-lookup/api/externalPackagesApi";
import { useExternalPackageLookup } from "@/features/external-package-lookup/api/useExternalPackageLookup";
import { useDeploymentFeatures } from "@/features/about/api/useDeploymentFeatures";
import { problemMessage } from "@/lib/problemMessage";

export function ExternalPackageLookupPage() {
  const { t } = useTranslation("external_package_lookup");
  const features = useDeploymentFeatures();
  const enabled = features.external_package_lookup === true;
  const intakeEnabled = features.intake_requests === true;

  const [searchParams] = useSearchParams();
  const [ecosystem, setEcosystem] = useState<ExternalPackageEcosystem>("npm");
  const [name, setName] = useState(() => searchParams.get("name") ?? "");

  const lookup = useExternalPackageLookup();

  if (!enabled) {
    return (
      <div className="flex h-full flex-col" data-testid="external-package-lookup-page">
        <PageHeader title={t("title")} description={t("subtitle")} />
        <EmptyState
          icon={<PackageSearch className="h-8 w-8" aria-hidden />}
          title={t("disabled.title")}
          description={t("disabled.description")}
          data-testid="external-package-lookup-disabled"
        />
      </div>
    );
  }

  const result = lookup.data;

  return (
    <div className="flex h-full flex-col" data-testid="external-package-lookup-page">
      <PageHeader title={t("title")} description={t("subtitle")} />

      <div className="space-y-6 px-6 py-4">
        <form
          className="flex flex-wrap items-end gap-3"
          data-testid="external-package-lookup-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) lookup.mutate({ ecosystem, name: name.trim() });
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="external-package-ecosystem" className="text-xs">
              {t("field.ecosystem")}
            </Label>
            <select
              id="external-package-ecosystem"
              className="flex h-10 w-40 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background transition-colors duration-fast ease-out-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              value={ecosystem}
              onChange={(event) =>
                setEcosystem(event.target.value as ExternalPackageEcosystem)
              }
              data-testid="external-package-ecosystem"
            >
              {EXTERNAL_PACKAGE_ECOSYSTEMS.map((slug) => (
                <option key={slug} value={slug}>
                  {t(`ecosystem.${slug}`)}
                </option>
              ))}
            </select>
          </div>
          <div className="min-w-[16rem] flex-1 space-y-1">
            <Label htmlFor="external-package-name" className="text-xs">
              {t("field.name")}
            </Label>
            <Input
              id="external-package-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={255}
              placeholder="lodash"
              data-testid="external-package-name"
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!name.trim() || lookup.isPending}
            data-testid="external-package-lookup-submit"
          >
            {t("submit")}
          </Button>
        </form>

        {lookup.isError ? (
          <Alert variant="destructive" data-testid="external-package-lookup-error">
            <AlertDescription>
              {problemMessage(lookup.error, t, { action: "errors.lookup_failed" })}
            </AlertDescription>
          </Alert>
        ) : null}

        {result && !result.found ? (
          <EmptyState
            icon={<PackageSearch className="h-8 w-8" aria-hidden />}
            title={t("not_found.title")}
            description={t("not_found.description")}
            data-testid="external-package-lookup-not-found"
          />
        ) : null}

        {result && result.found ? (
          <div className="space-y-4 rounded-lg border p-4" data-testid="external-package-lookup-result">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm break-all">{result.purl}</span>
                  {result.version ? (
                    <span className="text-xs text-muted-foreground">
                      {t("field.version_seen", { version: result.version })}
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-1">
                  {result.licenses.map((license) => (
                    <Badge key={license} variant="outline" data-testid="external-package-license">
                      {license}
                    </Badge>
                  ))}
                </div>
              </div>
              {intakeEnabled && result.purl ? (
                <Button asChild size="sm" variant="outline" data-testid="external-package-lookup-intake-cta">
                  <Link to={`/intake?purl=${encodeURIComponent(result.purl)}`}>
                    {t("intake_cta")}
                  </Link>
                </Button>
              ) : null}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {result.advisory_count === 0 ? (
                <Badge tone="success" data-testid="external-package-advisory-badge">
                  {t("advisories.none")}
                </Badge>
              ) : (
                <Badge tone="high" data-testid="external-package-advisory-badge">
                  {t("advisories.count", { count: result.advisory_count })}
                </Badge>
              )}
              {result.advisory_count > result.advisory_ids.length ? (
                <span className="text-xs text-muted-foreground">
                  {t("advisories.more", {
                    count: result.advisory_count - result.advisory_ids.length,
                  })}
                </span>
              ) : null}
              {result.advisory_ids.map((id) => (
                <Badge key={id} variant="outline" data-testid="external-package-advisory-id">
                  {id}
                </Badge>
              ))}
            </div>

            {result.homepage_url || result.source_repo_url ? (
              <div className="flex flex-wrap gap-4 text-sm">
                {result.homepage_url ? (
                  <a
                    href={result.homepage_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-brand hover:underline"
                    data-testid="external-package-homepage-link"
                  >
                    {t("field.homepage")}
                  </a>
                ) : null}
                {result.source_repo_url ? (
                  <a
                    href={result.source_repo_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-brand hover:underline"
                    data-testid="external-package-source-link"
                  >
                    {t("field.source")}
                  </a>
                ) : null}
              </div>
            ) : null}

            <div className="space-y-2 border-t pt-3">
              <h2 className="text-sm font-semibold tracking-tight">
                {t("internal_usage.title")}
              </h2>
              {result.internal_projects.length === 0 ? (
                <p className="text-sm text-muted-foreground" data-testid="external-package-internal-usage-empty">
                  {t("internal_usage.none")}
                </p>
              ) : (
                <ul className="space-y-1" data-testid="external-package-internal-usage-list">
                  {result.internal_projects.map((project) => (
                    <li key={project.project_id} className="text-sm">
                      <Link
                        to={`/projects/${project.project_slug}`}
                        className="text-brand hover:underline"
                      >
                        {project.project_name}
                      </Link>
                      <span className="ml-2 text-xs text-muted-foreground">
                        {t("internal_usage.version", { version: project.version })}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
