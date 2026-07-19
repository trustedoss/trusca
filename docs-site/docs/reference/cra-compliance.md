---
id: cra-compliance
title: EU Cyber Resilience Act — how TRUSCA helps
description: An honest mapping of the EU Cyber Resilience Act (Regulation (EU) 2024/2847) vulnerability-handling and reporting obligations to shipped TRUSCA capabilities — SBOM, VEX, remediation, exploitation signals — with the limits stated plainly.
sidebar_label: CRA compliance
---

# EU Cyber Resilience Act — how TRUSCA helps

:::note Audience
Security leads, product-security teams, and legal/compliance owners assessing
how TRUSCA supports **CRA vulnerability-handling obligations**. This page maps
regulation text to shipped features and states the limits honestly, in the same
spirit as the [comparison page](../comparison.md).
:::

:::warning Not legal advice, not a compliance certificate
TRUSCA is a tool that **supports** parts of a CRA programme. Using it does not
make a product CRA-compliant, and this page is not legal advice. Compliance is a
property of your organization's processes, documentation, and product — assess it
with qualified counsel against the regulation itself.
:::

## What the CRA requires (in scope for an SCA tool)

The Cyber Resilience Act (**Regulation (EU) 2024/2847**) sets cybersecurity
requirements for products with digital elements placed on the EU market. Two
parts of the regulation touch what an SCA/SBOM portal can help with:

- **Annex I, Part II — vulnerability-handling requirements.** Identify and
  document components and vulnerabilities (including an SBOM in a commonly used
  machine-readable format), remediate without undue delay, test regularly, and
  share vulnerability information.
- **Article 14 — reporting obligations.** Notify the coordinating CSIRT and
  ENISA of **actively exploited** vulnerabilities and severe incidents, on a
  tight timeline (an early warning within 24 hours).

The mapping below covers only these. Secure-by-design product requirements
(Annex I, Part I), update-distribution infrastructure, and the act of filing a
regulatory report are your product's and organization's responsibility, not a
scanner's.

## Obligation → TRUSCA capability

| CRA obligation (Annex I Part II / Art. 14) | How TRUSCA helps | Where |
|---|---|---|
| **§1 — Identify and document components and vulnerabilities, including an SBOM in a commonly used machine-readable format** | cdxgen detects components across 30+ ecosystems; Trivy correlates them against CVEs. SBOM export produces **CycloneDX and SPDX**, byte-stable. | [SBOM export](../user-guide/sbom.md), [components](../user-guide/components-and-licenses.md) |
| **§2 — Address and remediate vulnerabilities without undue delay** | Findings carry fixed-version data; remediation dry-run and pull-request generation propose the upgrade. The CI build gate fails the build (`exit 1`) on Critical CVEs / forbidden licenses so regressions do not ship. See the limit on **time-to-remediate tracking** below. | [Remediation PR](./remediation-pull-request.md), [dry-run](./remediation-dry-run.md) |
| **§3 — Apply effective and regular security tests and reviews** | Scheduled scans plus automatic re-matching: when the Trivy DB refreshes, a Celery beat re-scans existing SBOMs so newly published CVEs surface without a re-upload. | [Data sources](./data-sources.md), [scans](../user-guide/scans.md) |
| **§4 / §6 — Share vulnerability information; state exploitability** | VEX import/export communicates per-finding exploitability status (`not_affected`, `under_investigation`, …) in CycloneDX and SPDX, so downstream consumers get an accurate picture rather than raw CVE noise. | [VEX](../user-guide/vex.md) |
| **Art. 14 — identify actively exploited vulnerabilities** | The **KEV** flag (CISA Known Exploited Vulnerabilities) marks confirmed in-the-wild exploitation, and **EPSS** scores exploitation likelihood — the two signals that help you spot the findings a 24-hour report may hinge on. TRUSCA surfaces the signal; **it does not file the report**. | [Data sources](./data-sources.md), [triage](../user-guide/triage.md) |
| **Evidence and audit trail** | Every write action is recorded in the audit log (searchable, CSV export), and reports export to Excel/PDF for an evidence package. | [Audit log](../admin-guide/audit-log.md) |

## Limits — what TRUSCA does not do for you

Honesty first: the CRA is a process-and-product regime, and a scanner covers
only part of it.

- **Time-to-remediate / SLA tracking is not yet a first-class feature.** TRUSCA
  helps you *find and prioritize* vulnerabilities, but it does not yet track how
  long each finding has been open against a remediation deadline. "Without undue
  delay" is an SLA you must currently manage outside the portal. First-class
  finding-age and SLA-breach tracking is on the roadmap.
- **It does not file regulatory reports.** Identifying an actively exploited
  vulnerability (via KEV/EPSS) is not the same as notifying ENISA or a CSIRT
  within 24 hours. That workflow is yours.
- **It does not publish security advisories** or run your coordinated
  vulnerability disclosure (CVD) process. TRUSCA's own disclosure channel is in
  [`SECURITY.md`](https://github.com/trustedoss/trusca/blob/main/SECURITY.md);
  your product needs its own.
- **It does not distribute updates.** Secure, timely, free-of-charge delivery of
  security patches to your users is your product's responsibility.
- **Secure-by-design (Annex I Part I) is out of scope.** That is an engineering
  property of your product, not something a dependency scanner asserts.

## Practical checklist

A pragmatic way to use TRUSCA inside a CRA programme:

1. Generate and archive an **SBOM per release** (CycloneDX or SPDX) as your
   documented component inventory.
2. Enable the **CI build gate** so Critical CVEs and forbidden licenses cannot
   ship, and keep the **Trivy DB refresh + auto re-match** running so new CVEs
   against shipped SBOMs surface automatically.
3. Triage with **KEV and EPSS** to catch the actively-exploited findings that
   drive Article 14 reporting timelines.
4. Record exploitability decisions as **VEX** so downstream consumers and
   auditors see an accurate status, not raw CVE counts.
5. Keep the **audit log and report exports** as your evidence trail.
6. Manage **remediation deadlines** (time-to-remediate SLAs) in your own tracker
   for now — see the roadmap note above.

## References

- [Regulation (EU) 2024/2847 (Cyber Resilience Act)](https://eur-lex.europa.eu/eli/reg/2024/2847/oj)
- [How TRUSCA compares](../comparison.md) — the honest capability baseline
- [Vulnerability data sources](./data-sources.md) — where KEV/EPSS come from
- [VEX](../user-guide/vex.md), [SBOM export](../user-guide/sbom.md)
