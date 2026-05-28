import type { ReactNode } from "react";
import clsx from "clsx";
import Link from "@docusaurus/Link";
import Translate from "@docusaurus/Translate";
import styles from "./styles.module.css";

type FeatureItem = {
  id: string;
  title: ReactNode;
  description: ReactNode;
  href: string;
};

const FEATURES: FeatureItem[] = [
  {
    id: "vulns",
    title: (
      <Translate id="homepage.feature.vulns.title">
        Vulnerability tracking
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.vulns.desc">
        Trivy-backed CVE detection across NVD, OSV, GitHub Advisory, EPSS, and
        KEV. New CVEs trigger automatic re-matching of preserved SBOMs on a
        Celery beat — no manual rescan required.
      </Translate>
    ),
    href: "/docs/intro",
  },
  {
    id: "license",
    title: (
      <Translate id="homepage.feature.license.title">
        License compliance
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.license.desc">
        Declared licenses from cdxgen plus detected first-party licenses from
        scancode, classified allowed / conditional / forbidden. NOTICE files
        auto-generate; conditional licenses route through an approval workflow.
      </Translate>
    ),
    href: "/docs/admin-guide/users-and-teams",
  },
  {
    id: "sbom",
    title: <Translate id="homepage.feature.sbom.title">SBOM</Translate>,
    description: (
      <Translate id="homepage.feature.sbom.desc">
        CycloneDX (JSON / XML) and SPDX (JSON / Tag-Value) export, byte-stable,
        with per-component obligation tracking and a vulnerability PDF report.
        Excel and the compliance PDF are on the roadmap.
      </Translate>
    ),
    href: "/docs/intro",
  },
  {
    id: "containers",
    title: (
      <Translate id="homepage.feature.containers.title">
        Container scanning
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.containers.desc">
        Trivy scans container images for OS-package vulnerabilities alongside
        application dependencies — one risk view across source and runtime.
      </Translate>
    ),
    href: "/docs/intro",
  },
  {
    id: "ci",
    title: (
      <Translate id="homepage.feature.ci.title">CI/CD build gate</Translate>
    ),
    description: (
      <Translate id="homepage.feature.ci.desc">
        GitHub Actions and GitLab CI integrations (plus a Jenkinsfile example).
        Critical CVEs and forbidden licenses fail the build (exit 1); PR / MR
        comments post automatically.
      </Translate>
    ),
    href: "/docs/ci-integration/github-actions",
  },
  {
    id: "selfhosted",
    title: (
      <Translate id="homepage.feature.selfhosted.title">
        Self-hosted &amp; open
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.selfhosted.desc">
        Apache-2.0. Ships as Docker Compose for single-host deployments and a
        Helm chart for Kubernetes. Your data stays inside your network.
      </Translate>
    ),
    href: "/docs/installation/docker-compose",
  },
];

function Feature({ title, description, href }: FeatureItem): ReactNode {
  return (
    <article className={clsx("col col--4", styles.feature)}>
      <Link to={href} className={styles.featureCard}>
        <h3 className={styles.featureTitle}>{title}</h3>
        <p className={styles.featureDesc}>{description}</p>
        <span className={styles.featureLink}>
          <Translate id="homepage.feature.learnMore">Learn more →</Translate>
        </span>
      </Link>
    </article>
  );
}

export default function HomepageFeatures(): ReactNode {
  return (
    <section className={styles.features}>
      <div className="container">
        <header className={styles.sectionHeader}>
          <h2>
            <Translate id="homepage.features.title">
              One portal for OSS risk
            </Translate>
          </h2>
          <p>
            <Translate id="homepage.features.subtitle">
              Vulnerability, license, and SBOM workflows for engineering, legal,
              and security teams — without commercial per-seat pricing.
            </Translate>
          </p>
        </header>
        <div className="row">
          {FEATURES.map((feature) => (
            <Feature key={feature.id} {...feature} />
          ))}
        </div>
      </div>
    </section>
  );
}
