import type { ReactNode } from "react";
import Translate, { translate } from "@docusaurus/Translate";
import styles from "./styles.module.css";

type Shot = {
  id: string;
  src: string;
  alt: string;
  caption: ReactNode;
};

const SHOTS: Shot[] = [
  {
    id: "projects",
    src: "/img/screenshots/user-projects-list.png",
    alt: translate({
      id: "homepage.showcase.projects.alt",
      message:
        "Project portfolio list with per-project scan status and inline search, filter, and sort.",
    }),
    caption: (
      <Translate id="homepage.showcase.projects.caption">
        Portfolio view — every project, scan status, and risk at a glance.
      </Translate>
    ),
  },
  {
    id: "vulns",
    src: "/img/screenshots/user-vulns-list.png",
    alt: translate({
      id: "homepage.showcase.vulns.alt",
      message:
        "Vulnerability list showing CVE IDs, severity badges, CVSS, and VEX status workflow.",
    }),
    caption: (
      <Translate id="homepage.showcase.vulns.caption">
        Vulnerabilities — severity-ranked CVEs with a VEX status workflow.
      </Translate>
    ),
  },
  {
    id: "sbom",
    src: "/img/screenshots/user-sbom-tab.png",
    alt: translate({
      id: "homepage.showcase.sbom.alt",
      message:
        "SBOM tab with download buttons for CycloneDX JSON, CycloneDX XML, SPDX JSON, and SPDX Tag-Value.",
    }),
    caption: (
      <Translate id="homepage.showcase.sbom.caption">
        SBOM export — CycloneDX and SPDX, ready to download.
      </Translate>
    ),
  },
];

export default function HomepageShowcase(): ReactNode {
  return (
    <section className={styles.showcase}>
      <div className="container">
        <header className={styles.sectionHeader}>
          <h2>
            <Translate id="homepage.showcase.title">
              See it in action
            </Translate>
          </h2>
          <p>
            <Translate id="homepage.showcase.subtitle">
              A compact, information-dense UI built for engineering, legal, and
              security teams — risk-first, with detail drawers and inline
              filters throughout.
            </Translate>
          </p>
        </header>
        <div className={styles.grid}>
          {SHOTS.map((shot) => (
            <figure key={shot.id} className={styles.shot}>
              <div className={styles.frame}>
                <img
                  className={styles.image}
                  src={shot.src}
                  alt={shot.alt}
                  loading="lazy"
                  width={1456}
                  height={882}
                />
              </div>
              <figcaption className={styles.caption}>{shot.caption}</figcaption>
            </figure>
          ))}
        </div>
      </div>
    </section>
  );
}
