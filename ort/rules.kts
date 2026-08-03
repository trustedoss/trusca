/**
 * Minimal ORT evaluator ruleset for TrustedOSS Portal v2 dev/UAT.
 *
 * Returns an empty ruleSet so `ort evaluate` exits 0 with no violations.
 * License classification is driven by the SBOM (cdxgen) + the license
 * catalog seeded into Postgres — ORT here is just a contract honouring
 * stage that the scan task expects in the pipeline today.
 *
 * Replace with the full v1 ruleset (forbidden / conditional / allowed)
 * once the policy port lands.
 */
@file:Suppress("UNUSED_VARIABLE")

import org.ossreviewtoolkit.evaluator.*

val ruleSet = ruleSet(ortResult, licenseInfoResolver) {}
ruleViolations += ruleSet.violations
