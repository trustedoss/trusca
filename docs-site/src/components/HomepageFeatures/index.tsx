import type { ReactNode } from "react";
import clsx from "clsx";
import Link from "@docusaurus/Link";
import Translate from "@docusaurus/Translate";
import styles from "./styles.module.css";

type FeatureItem = {
  id: string;
  audience: ReactNode;
  title: ReactNode;
  description: ReactNode;
  href: string;
};

const FEATURES: FeatureItem[] = [
  {
    id: "ci",
    audience: (
      <Translate id="homepage.feature.ci.audience">For engineering</Translate>
    ),
    title: (
      <Translate id="homepage.feature.ci.title">
        Block risky builds in CI
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.ci.desc">
        A composite GitHub Action, a GitLab CI template, and a worked
        Jenkinsfile. Critical CVEs and forbidden licenses fail the build
        (`exit 1`); PR / MR comments post automatically with a per-finding
        breakdown.
      </Translate>
    ),
    href: "/docs/ci-integration/github-actions",
  },
  {
    id: "license",
    audience: (
      <Translate id="homepage.feature.license.audience">
        For legal &amp; compliance
      </Translate>
    ),
    title: (
      <Translate id="homepage.feature.license.title">
        Run license compliance at scale
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.license.desc">
        Allowed / conditional / forbidden classification, declared licenses
        from cdxgen plus detected first-party licenses from scancode, an
        approval workflow for conditional components, obligation tracking,
        and auto-generated NOTICE files.
      </Translate>
    ),
    href: "/docs/user-guide/components-and-licenses",
  },
  {
    id: "sec",
    audience: (
      <Translate id="homepage.feature.sec.audience">For security</Translate>
    ),
    title: (
      <Translate id="homepage.feature.sec.title">
        Triage vulnerabilities the way SOCs work
      </Translate>
    ),
    description: (
      <Translate id="homepage.feature.sec.desc">
        Trivy-backed detection across NVD + OSV + GitHub Advisory + EPSS +
        KEV. 7-state CycloneDX VEX triage, EPSS prioritization (column,
        sort, filter, policy gate), per-finding fix versions, an
        append-only audit log, and an automatic re-match beat that picks
        up new CVEs without a manual rescan.
      </Translate>
    ),
    href: "/docs/user-guide/vulnerabilities",
  },
];

function Feature({ audience, title, description, href }: FeatureItem): ReactNode {
  return (
    <article className={clsx("col col--4", styles.feature)}>
      <Link to={href} className={styles.featureCard}>
        <span className={styles.featureAudience}>{audience}</span>
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
              One portal, three jobs
            </Translate>
          </h2>
          <p>
            <Translate id="homepage.features.subtitle">
              Engineering blocks bad builds. Legal closes license risk.
              Security runs CVE triage. All in one self-hosted UI — no
              per-seat licensing.
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
