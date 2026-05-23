import type { ReactNode } from "react";
import clsx from "clsx";
import Link from "@docusaurus/Link";
import Translate, { translate } from "@docusaurus/Translate";
import useDocusaurusContext from "@docusaurus/useDocusaurusContext";
import Layout from "@theme/Layout";

import HomepageFeatures from "@site/src/components/HomepageFeatures";
import HomepageShowcase from "@site/src/components/HomepageShowcase";
import styles from "./index.module.css";

function HomepageHero(): ReactNode {
  const { siteConfig } = useDocusaurusContext();
  return (
    <header className={clsx("hero", styles.hero)}>
      <div className="container">
        <p className={styles.heroEyebrow}>
          <Translate id="homepage.hero.eyebrow">
            Apache-2.0 · Self-hosted · GA 2.0.0
          </Translate>
        </p>
        <h1 className={styles.heroTitle}>{siteConfig.title}</h1>
        <p className={styles.heroSubtitle}>
          <Translate id="homepage.hero.subtitle">
            Enterprise Software Composition Analysis — vulnerabilities, license
            compliance, and SBOMs in one self-hosted UI. No per-seat licensing.
          </Translate>
        </p>
        <div className={styles.heroCtas}>
          <Link
            className="button button--primary button--lg"
            to="/docs/installation/docker-compose"
          >
            <Translate id="homepage.hero.cta.install">
              Install in 5 minutes
            </Translate>
          </Link>
          <Link
            className="button button--secondary button--lg"
            to="/docs/intro"
          >
            <Translate id="homepage.hero.cta.docs">Read the docs</Translate>
          </Link>
          <Link
            className="button button--outline button--secondary button--lg"
            href="https://github.com/trustedoss/trustedoss-portal"
          >
            <Translate id="homepage.hero.cta.github">GitHub</Translate>
          </Link>
        </div>
        <dl className={styles.heroStats} aria-label={translate({
          id: "homepage.hero.stats.aria",
          message: "Project highlights",
        })}>
          <div className={styles.stat}>
            <dt>30+</dt>
            <dd>
              <Translate id="homepage.hero.stats.languages">
                languages &amp; build systems detected (cdxgen)
              </Translate>
            </dd>
          </div>
          <div className={styles.stat}>
            <dt>NVD · OSV · GHSA</dt>
            <dd>
              <Translate id="homepage.hero.stats.feeds">
                vulnerability feeds via Dependency-Track
              </Translate>
            </dd>
          </div>
          <div className={styles.stat}>
            <dt>EN · KO</dt>
            <dd>
              <Translate id="homepage.hero.stats.i18n">
                bilingual UI &amp; documentation at GA
              </Translate>
            </dd>
          </div>
        </dl>
      </div>
    </header>
  );
}

export default function Home(): ReactNode {
  const { siteConfig } = useDocusaurusContext();
  return (
    <Layout
      title={translate({
        id: "homepage.meta.title",
        message: "TrustedOSS Portal — Enterprise OSS Risk Management",
      })}
      description={siteConfig.tagline}
    >
      <HomepageHero />
      <main>
        <HomepageFeatures />
        <HomepageShowcase />
      </main>
    </Layout>
  );
}
