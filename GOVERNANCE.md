# Project Governance

This document describes how TRUSCA is governed: how decisions are made,
who makes them, and how someone becomes a maintainer. It complements
[`CONTRIBUTING.md`](CONTRIBUTING.md) (how to contribute) and
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) (how we treat each other).

## Decision-making model

TRUSCA operates on **maintainer consensus** with a lightweight
lead-maintainer tie-breaker. In practice:

- Most decisions are made in the open on issues and pull requests. A change
  merges when it has the approval of at least one maintainer and no unresolved
  objection from another maintainer.
- For significant or contentious changes (architecture, security model,
  breaking API or schema changes, dropping a feature), maintainers seek
  **consensus**. Consensus means no maintainer blocks the proposal after a
  good-faith effort to address concerns.
- If consensus cannot be reached, the **lead maintainer** (see
  [`MAINTAINERS.md`](MAINTAINERS.md)) makes the final call and records the
  rationale on the relevant issue.

This model keeps day-to-day work fast while ensuring that no single person
quietly changes the project's direction.

## Roles and responsibilities

### Contributors

Anyone who opens an issue, comments, reviews, or submits a pull request is a
contributor. No formal status is required. See [`CONTRIBUTING.md`](CONTRIBUTING.md)
to get started.

### Maintainers

Maintainers have write access to the repository and are responsible for:

- Reviewing and merging pull requests in their area of ownership
  (see [`MAINTAINERS.md`](MAINTAINERS.md) and [`.github/CODEOWNERS`](.github/CODEOWNERS)).
- Triaging issues and shepherding contributors.
- Upholding the quality, security, and release standards described in
  [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Following the security disclosure process in [`SECURITY.md`](SECURITY.md).

Maintainers are expected to act in the interest of the project and its users,
not any single employer or downstream.

### Lead maintainer

The lead maintainer is responsible for the overall direction of the project,
breaks ties when consensus fails, and stewards releases. The current lead
maintainer is listed in [`MAINTAINERS.md`](MAINTAINERS.md).

## Becoming a maintainer

Maintainership is earned through sustained, high-quality contribution. A
candidate is typically someone who:

- Has landed several substantive, well-reviewed pull requests.
- Has shown good judgment in reviews and issue triage.
- Understands the project's quality, security, and i18n standards.
- Is trusted by the existing maintainers to act in the project's interest.

Any maintainer may nominate a contributor by opening a private discussion among
maintainers. Promotion proceeds when there is consensus among existing
maintainers and the candidate accepts. New maintainers are added to
[`MAINTAINERS.md`](MAINTAINERS.md) and [`.github/CODEOWNERS`](.github/CODEOWNERS)
in the same pull request that records the decision.

## Stepping down and inactivity

Maintainers may step down at any time by opening a pull request that removes
them from [`MAINTAINERS.md`](MAINTAINERS.md). Maintainers who are inactive for
an extended period may be moved to emeritus status by maintainer consensus;
this is an administrative step, not a judgment of past contributions, and
re-activation is welcome.

## Proposing a change

The lifecycle for a non-trivial change is:

1. **Open an issue** describing the problem and the proposed direction.
2. **Discuss** on the issue or in [GitHub Discussions](https://github.com/trustedoss/trustedoss-portal/discussions)
   until the approach has rough agreement.
3. **Open a pull request** that implements the agreed approach, following
   [`CONTRIBUTING.md`](CONTRIBUTING.md).

Small, obvious fixes (typos, broken links, clear bugs) may skip straight to a
pull request.

## Roadmap and releases

The public roadmap is [`ROADMAP.md`](ROADMAP.md). Roadmap items are proposals,
not commitments. Releases follow [Semantic Versioning](https://semver.org/) and
are recorded in [`CHANGELOG.md`](CHANGELOG.md).

## Code of Conduct

All participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md). Maintainers are responsible for
enforcing it fairly and consistently.

## Changing this document

This governance document may itself be changed through the normal pull-request
process, subject to maintainer consensus.
