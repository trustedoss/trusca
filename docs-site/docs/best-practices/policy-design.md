---
id: policy-design
title: Policy design
description: Design a license and vulnerability gate policy in TRUSCA — license tiers, the approval workflow for conditional licenses, gate thresholds, and start-permissive-then-tighten.
sidebar_label: Policy design
sidebar_position: 2
---

# Policy design

A policy is the contract between "what the scanner found" and "what blocks a build". Get it wrong in one direction and every build turns red on day one and the team routes around the gate; wrong in the other and the gate never blocks anything. This page helps you design a license and vulnerability policy that is enforceable from the start and tightens over time.

:::note Audience
`team_admin` designing a team policy, and `super_admin` setting an organization-wide default. Familiarity with the [license classification tiers](../reference/license-policies.md) and the [build gate](../reference/env-variables.md#build--policy-gate). Software Composition Analysis (SCA) is the practice of inventorying and risk-scoring open-source dependencies, which is what these policies govern.
:::

## What the gate actually evaluates {#what-the-gate-evaluates}

Two independent conditions fail a build out of the box, plus one optional dimension:

| Condition | Blocks by default | Tunable |
|---|---|---|
| A component with a **forbidden**-tier license | Yes | Per-team [license policy](../reference/license-policies.md) |
| An open finding at **Critical** severity | Yes | Fixed — not env-driven |
| An open finding with a high **EPSS** probability | No | `GATE_EPSS_THRESHOLD` (opt-in) |

Design the license side and the vulnerability side separately — they use different levers.

## License tiers: the three buckets {#license-tiers}

Every license resolves to one of three tiers. This is the spine of license policy:

| Tier | Meaning | Gate behaviour | Examples |
|---|---|---|---|
| **Forbidden** | Blocks the build | Fail | AGPL, GPL, SSPL, BUSL |
| **Conditional** | Needs legal review + approval | Pass, but raises an approval request | LGPL, MPL, EPL, CDDL |
| **Permitted** | Use freely | Pass | MIT, Apache-2.0, BSD, ISC |

Without a policy the portal classifies against a fixed built-in catalog. A [license policy](../reference/license-policies.md) makes that classification editable data — you can forbid a normally-permitted license, waive a normally-forbidden one for a single component, or set the posture for uncatalogued licenses — all at runtime, no redeploy. Resolve the scope precedence (team policy, else org default, else built-in catalog) as described in [effective policy resolution](../reference/license-policies.md#effective-policy-resolution).

:::tip Set an org default, override per team
Author one **org-default** policy as `super_admin` for the house rules, then let each `team_admin` override only where a team genuinely differs (for example, a team shipping a closed-source SaaS can treat copyleft more permissively than a team shipping a redistributed binary). Do not hand every team a blank policy — the org default is the safety net for a team that never authors one.
:::

### The uncatalogued-license posture is a real decision {#unknown-posture}

`unknown_license_category` decides how a license absent from both the catalog and your overrides is treated. The default is `conditional` — an unknown license routes to approval rather than silently passing or hard-blocking. Keep it there unless you have a specific reason: `allowed` lets genuinely unknown terms ship unreviewed, and `forbidden` turns every misparsed or novel license into a build failure.

## When to use the approval workflow {#approval-workflow}

Conditional licenses do not block the build — they raise a request on the [Approvals](../user-guide/approvals.md) queue that a reviewer disposes through the [state machine](../user-guide/approvals.md#state-machine) (Pending → Under Review → Approved / Rejected). Use it when the answer is "it depends on how we use it", not "always yes" or "always no":

- **Always yes for us** → override the license to `allowed` in the policy, or add a `license_exception`. No per-component review.
- **It depends on the linkage / distribution model** → leave it `conditional` and let legal decide per component. This is the workflow's purpose.
- **Never** → override the license to `forbidden`. The build blocks; no review needed.

:::warning A Rejected approval does not block the build in this release
The build gate evaluates the `forbidden` tier only. Marking an approval **Rejected** records the verdict for audit but does **not** auto-promote the component to forbidden, so a later scan still passes it. If a component must block CI, override its license to `forbidden` in the policy — do not rely on the Rejected verdict. See the [Rejected verdict caveat](../user-guide/approvals.md#rejected-verdict).
:::

Because the same component in two projects raises two independent requests, decide up front whether a verdict is project-specific or house-wide — see [cross-project approvals](../user-guide/approvals.md#cross-project-approvals). The obligations a conditional license carries (attribution, source disclosure, copyleft) are enumerated in the [obligation catalog](../reference/obligation-catalog.md#by-license-category); a reviewer reads them straight off the approval drawer.

## Vulnerability gate thresholds {#vuln-thresholds}

The severity model is fixed: **Critical** open findings block, everything below is informational at gate time. See the [severity model](../user-guide/vulnerabilities.md#severity-model). Two levers shape what "open" means and what else blocks:

- **Triage discipline (the main lever).** Only *open* findings count. A finding you have dispositioned through the [VEX state machine](../user-guide/vulnerabilities.md#vex-state-machine) — `Not affected`, `False positive`, `Fixed` — is excluded from the gate. A healthy gate depends on the team actually triaging, not on lowering the bar. VEX is the Vulnerability Exploitability eXchange model that records that triage.
- **EPSS gate (optional).** Set `GATE_EPSS_THRESHOLD` (0–1) to also fail the build when an open finding's exploit probability crosses the line, even if it is not Critical. Leave it unset until the team is comfortable triaging — see [`GATE_EPSS_THRESHOLD`](../reference/env-variables.md#build--policy-gate).

## Start permissive, then tighten {#start-permissive}

A policy that blocks every build on rollout gets disabled or bypassed within a week. Ratchet instead:

1. **Observe.** Roll the gate out in report-only spirit — keep the default thresholds, do not add overrides, and let the team see verdicts on PRs without a wall of red. Watch the dashboard for the real distribution of licenses and severities.
2. **Codify the house rules.** Author the org-default policy from what you observed: forbid what legal already forbids, set the uncatalogued posture, add exceptions for the handful of waived dependencies you actually ship.
3. **Tighten per team.** Where a team's risk profile warrants it, override toward `forbidden` and, once triage is a habit, enable the EPSS gate. Each tightening step is a policy edit — no redeploy — so you can move one team at a time.

:::caution Tightening reclassifies existing components
Enabling a stricter policy re-evaluates every component's license expression on the next scan. A dependency that passed yesterday can block today. Announce the change, and stage it per team rather than flipping the org default under everyone at once.
:::

## Verify it worked

<!-- docs-uat: id=bp-policy-design-review kind=manual tier=manual -->
Sanity-check the policy you designed:

<!-- docs-uat: id=bp-policy-design-1 kind=manual tier=manual -->
1. An org-default policy exists and a team with no policy of its own inherits it (the team's effective-policy read returns the org default, not a `404` fallback to the static catalog).
<!-- docs-uat: id=bp-policy-design-2 kind=manual tier=manual -->
2. A component you overrode to `forbidden` fails the build gate on the next scan; one you added as a `license_exception` passes.
<!-- docs-uat: id=bp-policy-design-3 kind=manual tier=manual -->
3. A conditional-license component raises an approval request rather than blocking — confirming the tier split is wired.
<!-- docs-uat: id=bp-policy-design-4 kind=manual tier=manual -->
4. The `unknown_license_category` posture is a deliberate choice (default `conditional`), not left unconsidered.
<!-- docs-uat: id=bp-policy-design-5 kind=manual tier=manual -->
5. If you enabled the EPSS gate, a finding above `GATE_EPSS_THRESHOLD` fails the build while a lower-probability finding of the same severity does not.

## See also

- [License policies](../reference/license-policies.md) — tiers, scopes, overrides, exceptions, and the editor
- [Obligation catalog](../reference/obligation-catalog.md#by-license-category) — duties per license category
- [Approvals](../user-guide/approvals.md) — the conditional-license workflow
- [Vulnerabilities](../user-guide/vulnerabilities.md#vex-state-machine) — VEX triage that shapes the gate
- [Environment variables — Build / policy gate](../reference/env-variables.md#build--policy-gate) — `GATE_EPSS_THRESHOLD`
