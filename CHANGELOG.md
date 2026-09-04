# Changelog

All notable changes to TrustedOSS Portal are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Erasing a user's personal data, behind two people and an operator.** A
  super admin opens a request against a user; a different super admin approves
  it; an operator then runs `python -m scripts.anonymise_user` with the owner
  database credential. The command clears the account's email and name,
  removes its OAuth links, sessions and saved searches, renames a personal team
  named after the person, and clears `ip` and `user_agent` from the subject's
  audit rows. The account row survives, because audit entries reference it and
  that reference is what keeps the trail saying who rather than somebody, but
  no route authenticates it afterwards.

  Reaching inside `audit_logs` needs an exception to the append-only trigger,
  and it is the narrowest shape that does the job: the caller must be a member
  of the table's owner role, the two columns may only move toward NULL, every
  other column must be unchanged, and the function refuses unless an approved
  request exists. `diff` is deliberately outside the exception, so an address
  written into a diff before this version stays there. What the operation does
  not erase is written down rather than glossed: see the admin guide.

  Approval and execution are separated on purpose, which leaves a state where
  two people have agreed and nothing has happened yet. **Administration →
  Health** lists those requests with how long each has waited, because an
  erasure request usually carries a deadline and a backlog nobody can see is
  the way that deadline passes.

- **`GET /v1/users/me/export`.** Any signed-in user can download the personal
  data held about them: account, notification preferences, sign-in methods,
  team memberships, saved searches, and their own activity record. Work
  product is not included, and change contents are stripped from the activity
  record because an entry describing a change to another user carries that
  user's data. The activity record is capped, and when it is capped the
  payload says so and gives the true total.

- **Three indexes the hot read paths were missing.** Opening a vulnerability
  ran its history panel through `ix_audit_logs_target_table` alone, which
  matches `target_table` and leaves `target_id` to a heap filter over the
  largest bucket that column has, because the scan pipeline writes one create
  row per finding. Measured on 200,000 audit rows, reading five: 3,309 buffers
  and 28,566 rows discarded, 116 ms, now 52 buffers and 2.7 ms. The
  actor-filtered admin audit search sorted its results, and under a `LIMIT`
  the planner preferred walking the time index backwards and discarding every
  other actor's rows: 4,734 buffers, now 44. The active-project list, which
  the dashboard also reads, sorted every project a team owns because the index
  covering its two predicates carried no `updated_at`: 802 buffers over a
  3,000-row sort on 60,000 projects, now 37 with a partial index on the
  unarchived half.

  **Upgrade note.** Migration `0078` builds all three in a transaction, which
  takes a write lock on `audit_logs` and `projects` for the duration.
  `CREATE INDEX CONCURRENTLY` cannot run inside one, so this matches every
  other index revision in the tree. On a small deployment the build is
  imperceptible; on one with a large `audit_logs`, either upgrade inside a
  maintenance window or pre-build the three indexes online beforehand, after
  which the migration finds them already present. The statements are in the
  migration's own docstring.

- **A configured EPSS threshold that decides nothing now says so.** The gate
  reported `epss_gate_count: 0` both when nothing scored above the threshold
  and when nothing was scored at all, and passed the build either way. On a
  deployment with no EPSS data, which is every deployment that has not turned
  the daily sync on, an operator who set a threshold had their intent
  discarded in silence and the pass looked like a verdict. The gate result now
  carries `epss_outcome`: `not_configured`, `evaluated`, `partial` (some open
  findings carry no score, so the count is not a complete answer) or `no_data`
  (none do, so the axis judged nothing). The CI action exposes it as
  `epss-outcome` and writes a job-summary row and a warning annotation, the
  project's gate card says the axis was not evaluated instead of drawing a
  zero, and the team policy editor says when the deployment has nothing behind
  a threshold it is showing, distinguishing a sync that was never switched on
  from one that has stopped landing.

  `GATE_EPSS_ON_MISSING_DATA` decides the verdict. It defaults to `allow`,
  which is what every deployment did before, so upgrading changes no build's
  result; `block` fails the build instead, so a configured threshold cannot be
  ignored. `block` applies to `no_data` only and deliberately not to
  `partial`: EPSS does not score every CVE and gaps are normal even with a
  healthy sync, so an option that fired on them would be switched off in the
  first week and protect nothing after that.

- **EPSS scores are now actually collected, so the features built on them
  work.** `vulnerabilities.epss_score` and `epss_percentile` have existed
  since v2.4 and nothing ever wrote them: the scanner emits no EPSS on either
  the SBOM or the image path, measured across 88 and 107 live findings with
  zero EPSS keys, and every row the scanner had created was NULL. So the EPSS
  column and the `min_epss` filter on the Vulnerabilities tab showed nothing,
  the EPSS term in `sort=priority` never moved a ranking, the reports carried
  a blank column, and `GATE_EPSS_THRESHOLD` passed every build no matter how
  low it was set, which is worse than an absent gate because the pass looks
  like a verdict. A daily beat now syncs the scores from FIRST's published CSV
  onto the CVEs this deployment has actually seen.

  Off by default (`EPSS_REFRESH_ENABLED`), following the EOL and
  malicious-package feeds rather than KEV: installing the product should not
  reach the public internet on its own, and an air-gapped deployment can point
  `EPSS_FEED_URL` at an internal mirror. While it is off those surfaces stay
  empty, which the data-sources reference now says plainly instead of implying
  the scores are always there.

  The bulk CSV is the only ingest path and the API is deliberately not used:
  FIRST's own guidance is that their lookup API must not be used for bulk
  downloads or to keep a local copy in sync. Attribution and terms are
  recorded in `THIRD_PARTY_NOTICES.md`, in the data-sources page and in the
  feed client. Nothing is redistributed: each installation downloads the file
  itself.

  A sync reads the whole feed but keeps only the CVEs already in the catalog,
  so peak memory tracks the deployment rather than the 367,000-row document,
  and it writes only the rows whose score actually moved. A document that
  parses to implausibly few rows is refused before any write, so a truncated
  publish upstream cannot blank good scores.


- **A scan that finds nothing now says so, instead of looking like a clean
  project.** Nothing on the scan path counted components, so a tree cdxgen
  could not parse produced an empty SBOM, exited 0, and finished `succeeded`:
  the project then showed no components, no licences and no CVEs, the build
  gate passed because every count it reads was 0, and the Components tab
  showed the never-scanned empty state to somebody who had just scanned. Such
  scans still succeed, because for a build system the scanner does not read an
  empty result is the correct answer and failing there would break working
  pipelines. What is new is that the scan records which kind of empty it was:
  `empty_no_manifests` when the source declared no dependency manifest either,
  and `empty_with_manifests` when it did, which points at a scan failure
  rather than an empty project. Surfaced on the project overview, on the
  Components tab, as `component_outcome` on the gate result, and as a warning
  next to the verdict in the GitHub Action's job summary. Under-reporting (a
  manifest without its lockfile, which yields the direct dependencies and
  drops the transitive ones) is deliberately not folded in: it is a populated
  SBOM with a different cause and a different fix.

### Removed

- **BREAKING (API): `vuln_data_available` is gone from
  `GET /v1/projects/{id}/overview`.** The field reported whether the
  vulnerability database held any data when the anchored scan ran, and it was
  derived from a `scan_metadata` key that nothing has written since
  Dependency-Track was replaced at v0.10.0. It has therefore been `null` on
  every response since, and the UI caveat it drove rendered only on `false`,
  so it has never appeared. Reviving it has no target: Trivy fails the scan
  outright when its database is unusable, so the failure mode the caveat
  warned about no longer exists. A field that is always `null` looks like a
  guarantee that is not being made, so it is removed rather than left. A
  consumer that reads the key defensively is unaffected; one that requires it
  to be present will break. The caveat it was meant to carry is now served by
  `component_outcome` below.

### Fixed

- **A private certificate authority could not be configured for `git clone`.**
  The worker forwards `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE` and
  `NODE_EXTRA_CA_CERTS` to every tool it runs, and git reads none of them.
  Measured in the worker image: pointed at a path that does not exist,
  `GIT_SSL_CAINFO` and `GIT_SSL_CAPATH` make `git ls-remote` exit 128 while the
  other three leave it at 0. On a network with an internal authority, a source
  scan therefore stopped at its first step no matter how the rest was
  configured. Both are now forwarded. `CURL_CA_BUNDLE` deliberately is not:
  nothing we run reads it, and an operator who saw it arrive would conclude the
  certificate was configured.

- **The same variable adds a certificate for some tools and replaces the trust
  set for others, silently.** The Go tools keep reading the system certificate
  directory when only `SSL_CERT_FILE` is set, so there a private authority is
  added to the public roots. The portal's own outbound calls build their trust
  from that file alone. Point it at a bundle holding only a corporate authority
  and scans keep working while the vulnerability feeds, Slack, GitHub and the
  ticket webhook quietly become unverifiable, which reads as a feed outage
  rather than a certificate setting.

  The backend now states its trust set on every boot: how many certificate
  authorities its own calls will accept, how many it ships with, and which
  setting decided. Unconditional rather than only on suspicion, because
  trusting a private authority alone is a legitimate configuration and a
  warning that fires on a correct setup is one somebody turns off. The warning
  is layered on top for the case that is almost certainly a mistake, and says
  what to do about it. Where a certificate directory is configured the count
  reads as unknown rather than zero: such a store loads certificates on demand
  and reports none until it does.

  A new admin-guide page covers the whole setup for Docker Compose, including
  the two limits this release does not lift: the Helm chart takes extra
  environment variables but not extra volumes, and image pulls made by the
  Docker daemon are outside the portal's environment entirely.

- **A finding's assignee, deadline and ticket lasted about six hours.** The
  four columns were written and read correctly everywhere, but nothing carried
  them onto the row that replaced the one they were on. Findings are per-scan
  rows: a rescan inserts new ones, and the rematch beat deletes and re-inserts
  every succeeded scan's findings on a six-hour default cadence that nobody
  triggers. So an assignment made in the morning was gone by the afternoon,
  while the user guide promised without qualification that a finding carries
  who is fixing it and by when. The SLA clock and the analyst's verdict were
  already carried forward; these were not, which is what made it read as an
  oversight rather than a decision.

  They are now carried the same way, keyed on the same (component version, CVE)
  pair, so upgrading a package still starts its findings unowned. The one
  difference from the verdict is deliberate: the verdict lookup skips
  undecided rows so a scan that ran before the carry-forward existed is stepped
  over, and doing that here would mean skipping rows with nobody on them. A row
  has nobody on it both when the feature was missing and when somebody removed
  the person on purpose, and restoring an assignment that was deliberately
  removed would be a worse defect than the one being fixed, so the most recent
  row wins whatever it holds.

  A guard now compares the finding PATCH request models against the
  carry-forward set, so opening a new column for editing and forgetting it
  fails a test rather than losing data later. The same shape had already cost
  the analyst's verdict once before this.

- **The last super admin could be demoted from behind a table the caller
  made.** The database trigger that refuses to leave a deployment with zero
  active super admins counted the survivors with an unqualified `FROM users`,
  and its function pinned no search path. PostgreSQL looks in the temporary
  schema first when it resolves a relation, and TEMP is granted to everyone,
  so a caller could create a temp table called `users`, fill it with rows that
  look like other super admins, and demote the real last one: the count came
  from their own table and the guard saw company. The result is an instance
  nobody can administer, undoable only with the owner database credential.

  This needed no extra privilege. The application role already holds UPDATE
  and DELETE on `users`, which is how it manages accounts. The function now
  pins `search_path` to `pg_catalog, public, pg_temp` and names the table with
  its schema; either half stops it alone, and listing `pg_temp` is the part
  that is easy to omit because omitting it does not demote the temporary
  schema, it just fails to mention it.


- **The workspace archive is now bounded by the backup timeout, like the dump
  already was.** `BACKUP_SUBPROCESS_TIMEOUT` reached the `pg_dump` subprocess
  and not the workspace tar, because that step is Python `tarfile` and nothing
  was checking a clock. The Helm chart sizes this worker's
  `terminationGracePeriodSeconds` on the premise that both long steps are
  bounded by that setting, so a workspace large enough or a filesystem slow
  enough ran past the grace period and was killed, on exactly the deployments
  that can least afford it. The deadline is checked once per file, so a single
  file larger than the remaining budget still overshoots by one file rather
  than by the whole tree. The chart comment, which described the tar as a
  subprocess, now says what it is.

- **A half-written backup artifact no longer carries the name a restore reads.**
  The dump and the workspace archive were written straight to
  `postgres.sql.gz` and `workspace.tar.gz`. A process killed partway leaves no
  Python running to tidy up, so a truncated file sat in the backup directory
  under a name that means "this is the dump". They are now written under a
  temporary name and moved into place once complete, which is a rename inside
  one directory and therefore atomic: the name either does not exist or names a
  finished file. This does not replace the manifest being written last, since a
  run that fails for reasons unrelated to a partial file leaves the same state.

- **A backup that did not finish now says so in the listing.** The manifest is
  written last, after both artifacts and their checksums, so a run killed
  partway leaves a directory without one. The restore already refused such a
  backup; the listing showed it as an ordinary row, so an operator counting
  restore points counted one that would not restore and found out on the day
  it mattered. The listing carries a `complete` flag and the admin page marks
  the row, and the listing and the restore now read one list of required
  artifacts rather than two copies of it.

  The checksum re-verify also skipped any artifact the manifest did not
  mention, in the same branch that skipped an artifact that was not there. Only
  the pre-flight's insistence on a manifest kept an unaccounted-for file out,
  so two defences were really one, and relaxing the pre-flight later would have
  taken the checksum check with it unnoticed. A file that is present and
  unlisted is now refused, while a manifest with no checksums at all is still
  accepted, because `scripts/backup.sh` wrote that shape and those backups are
  real restore points. That script now records checksums too, so the path we
  ship stops producing backups that skip verification, and a contract test runs
  both producers' manifest code and compares them.

- **A personal team carried its owner's name or email address in its own
  name.** Signing in through OAuth for the first time creates a personal team,
  and it was named `{full_name}'s Team`, falling back to the email's local
  part when the provider sent no name. Its description read `Personal team for
  <address>`. Both are personal data and both are on screen rather than in a
  log: the team list, the team switcher, every membership row. The name now
  carries an identifier, `Personal team (<id prefix>)`, matching the
  `Personal org (<suffix>)` built beside it from the same user id, and the
  description is left empty because its only content was the address.

  **Teams created before this version keep their existing names.** Renaming
  them in a migration would change a label people recognise, on data whose
  owner has not asked for anything, so it is left to the operator: an admin
  can rename a team from the teams screen. Anonymising a user clears their own
  personal team's name and description as part of the erasure.

- **The backup timeout now bounds the backup.** `BACKUP_SUBPROCESS_TIMEOUT` is
  documented, settable, read at run time and passed to `proc.wait()`, and it
  bounded nothing: the two calls ahead of that wait read the child's pipes to
  EOF, and EOF only arrives once the child has exited. A `pg_dump` stalled on
  storage or on a lock never reached the wait, so the `kill()` in its timeout
  handler was unreachable and the task never raised, which also means nothing
  was recorded as failed while a worker slot stayed occupied. The nightly beat
  would go quiet rather than report. Both pipes are now drained on their own
  threads and the bounded wait is the only thing the main thread does.

  The same two calls could deadlock outright. With stdout being copied and
  stderr unread, a dump emitting more warnings than a pipe buffer holds (about
  64 KiB) stops writing and never finishes the output the copy is waiting on.
  The restore path had it worse: it opened `psql`'s stdout as a pipe and read
  it nowhere, staying under the buffer only because `psql` runs with `--quiet`.
  Neither showed up in testing, because the existing round-trip test exercises
  the path where nothing goes wrong, and on that path the old code was fine.

- **Changing a password now ends the sessions that were already open.** A
  reset revoked the user's refresh tokens, which stopped renewal, and left the
  access token an attacker was already holding to run out its own thirty
  minutes. Access tokens minted before the change are now refused, using a
  `users.password_changed_at` stamp compared against the token's issue time,
  so there is no revocation list to keep and no extra query to make. Existing
  rows are left unstamped rather than backfilled, because backfilling would
  have logged out every user of a running deployment at upgrade.

  Revoking "every active refresh token" also only covered the rows that
  existed when the sweep ran. A login, a token renewal or an OAuth callback
  that read its inputs just before the reset and inserted its row just after
  left a live session the reset had never seen, and its holder could renew
  from there into an access token new enough to pass the check above. All
  three now take the same per-user lock before writing, and on the far side of
  it a path whose credential has since been replaced is refused instead of
  opening a session. `/auth/refresh` also gained a rate limit
  (`REFRESH_RATE_LIMIT`, 30/minute by default), because minting tokens on
  demand was how the boundary was reached.

- **A session scope that writes and never commits now says so.** Two sweep
  tasks fixed rows in memory, counted the fixes into their summaries, and
  returned success while the database kept its old values, because
  `sync_session_scope` leaves the commit to the caller and neither caller made
  one. Those two are fixed; this closes the class. A static check cannot find
  it, because the write may sit in the task and the commit in a service the
  task calls, so reading files flags eight tasks that correctly delegate their
  writes and catches nothing real. The scope now asks the session at close
  whether it emitted DML that was never committed and logs a warning when it
  did; a deliberate rollback answers the question, so the dry-run paths stay
  quiet. The test suite turns that warning into a failure, so a task written
  without its commit fails in CI instead of shipping and doing nothing.




- **The dashboard stopped counting at 100 projects, and undercounted risk in
  the dangerous direction.** Every KPI and both distribution charts were
  computed in the browser from one page of `GET /v1/projects`, whose `size`
  caps at 100. Past that the numbers were not merely incomplete: the list is
  ordered `updated_at DESC`, so what fell off the end was whatever nobody had
  touched lately, which is where risk accumulates. Measured on a 105-project
  seed with every critical finding on the 5 least recently updated projects,
  the dashboard showed `critical: 0` against a real 5. The page now reads
  `GET /v1/dashboard/summary`, which has always aggregated the whole
  portfolio; the endpoint gains project-shaped severity and licence counts,
  because the charts count projects while the KPI beside them counts
  components. The "no projects yet" state also moves to the server count, so a
  failed project-list request no longer renders as an empty portfolio.

- **Two workers scanning at once could drop a scan's vulnerability findings
  without failing the scan.** Both workers miss the same CVE in the
  vulnerability catalog and both insert it; the loser's flush hits the unique
  constraint on `vulnerabilities.external_id`. Recovering from that rolled the
  whole session transaction back, which discarded every finding already staged
  in it. Nothing raised, so the scan still reported success while the count it
  logged disagreed with the rows a user could see, and the create audit rows
  for the discarded findings stayed behind, naming detections that no finding
  row backed. The catalog insert now runs in a SAVEPOINT, so only the losing
  insert is undone; the audit writer now asks the session whether a row backs
  a finding before recording it, because a rolled-back INSERT leaves the
  object transient with its assigned key still readable; and the per-scan
  summary log carries `catalog_races` and `audits_emitted` counts. On the rematch
  path, where the same transaction had already deleted the scan's prior
  findings, the session-wide rollback also restored them and the re-insert then
  failed the finding uniqueness constraint, taking the whole rematch down
  (#290). The container persister carried the same unguarded insert and
  failed the whole scan on it; it now goes through the same shared upsert,
  which also gets it the reference sanitisation and the stale-row refresh
  the source path already had.

## [0.22.4] - 2026-09-02

### Fixed

- **A full disk could take the whole stack down while the dashboard
  reported room to spare.** The scan disk guard and the admin disk panel
  both read `WORKSPACE_HOST_PATH` and nothing else. That is only the right
  filesystem while the workspace shares one with everything else a scan
  writes; move the workspace to network storage, which is how a corpus
  larger than one host's disk gets scanned, and both report the network
  volume while the toolchain caches fill the container's own. Observed: a
  26 GB root partition at 100% with the guard reporting 12%, no scan ever
  refused, and the container runtime down with the disk. The guard now
  checks the workspace plus `DISK_GUARD_EXTRA_PATHS` (default `/`) and
  names the offending path in its 503; the panel gains a `root_fs` card
  (#273).
- **The worker toolchain caches were unbounded and, on Compose, invisible.**
  `Dockerfile.worker` pins every tool's cache under the worker's `HOME` so
  one mount covers them all, and the Helm chart mounts an `emptyDir` there,
  but `docker-compose.yml` mounted nothing: the caches went to the
  container's writable layer, absent from `docker system df` and
  unclearable without recreating the container. Nothing anywhere ever
  deleted a byte of them, and they grow with the number of distinct
  dependencies a deployment has ever scanned. Adds a named cache volume per
  worker and `trustedoss.toolchain_cache_cleaner`, a 6-hourly beat holding
  them under `TOOLCHAIN_CACHE_MAX_BYTES` (default 8 GiB) that never touches
  a cache written to within `TOOLCHAIN_CACHE_IDLE_SECONDS` (#274).
- **A scan whose worker died stayed `running` forever.** Scan tasks mark
  their own row terminal on every in-process exit path, which does not
  cover a hard-time-limit SIGKILL, an OOM kill, or a container runtime
  restart. The abandoned row holds the project's active-scan index slot, so
  every retry of that project 409s, and one of the team's concurrency
  slots, both silently. Adds `trustedoss.stale_scan_reaper`, a half-hourly
  beat that fails scans older than the hard time limit plus
  `STALE_RUNNING_SCAN_GRACE_SECONDS`, since no live task can outlive that
  limit. Queued scans are never reaped: queue wait is unbounded under
  backlog (#275).

### Added

- Project list rows show a GitLab-style `{team} / {project}` breadcrumb and
  the toolbar gains a team filter, both whenever the loaded page spans more
  than one team. A cross-team list (a super admin's, which no active-team
  filter narrows) otherwise renders identically-named projects with nothing
  telling them apart (#272).

## [0.22.3] - 2026-08-31

### Security

- **A team or an OAuth signup could end up attached to a stranger's
  organization.** Organization is documented as a deployment-level
  singleton, but self-signup and OAuth signup each create a personal
  organization for tenant isolation of org-scoped data. Two admin-facing
  paths still assumed the singleton model and silently picked or reused
  "the" organization regardless of how many existed - once a member joined
  such a team, they could read that stranger's organization-scoped
  verdicts. Admin team creation now refuses to guess whenever more than one
  organization exists, and OAuth signup creates its own personal
  organization instead of reusing one. A new `Organization.is_personal`
  column (backfilled for existing deployments) closes two further gaps a
  security review surfaced in the initial fix: a demo deployment sitting at
  exactly one, personal, organization right after its first self-registration,
  and an explicit organization override with no awareness of which
  organizations are personal (#257).

### Fixed

- **The first team an operator created after `create_super_admin` always
  failed with a 422.** Bootstrap only inserted a super-admin `User` row and
  never an `Organization`, so the single-org deployment model had nothing
  for the first team to attach to. `create_super_admin` now provisions the
  organization it needs, reusing an existing one if the command runs again
  (#246).
- **A scan's build tooling could survive its own timeout as an unreaped
  zombie process.** The streaming subprocess helper called `kill()` on
  timeout but never followed up with `wait()`, leaving the killed child for
  the kernel to hold onto indefinitely once its parent moved on. It now
  waits for the child after killing it, the same pattern already used
  elsewhere in the codebase (#245).
- **A fully offline Trivy DB cache (`TRIVY_DB_BOOTSTRAP_ON_START=false`)
  still made every scan try to phone home for a database update check**,
  stalling instead of using the pre-populated cache the offline-install
  guide promises. Scan-time Trivy calls now pass `--skip-db-update` in that
  mode (#248).
- **The admin panel could report a Trivy database as "fresh" that the
  Trivy CLI itself would refuse to use.** Freshness only checked the
  metadata's update timestamp and version, ignoring a zero-value
  `DownloadedAt` - the CLI's own signal that a download never actually
  completed. The panel now treats that shape as if the metadata were
  missing (#247).
- **In a role-separated deployment, 21 tables added since the runtime
  role's original grant could not be updated or deleted by the runtime
  at all**, surfacing as unhandled 500s on ordinary writes - most
  seriously, a leaked GitHub App key could not be revoked. Every
  affected table's actual read/write needs were verified against the
  service and task code and granted explicitly (#249).

### Added

- **`SCAN_LOCAL_DOCKER_ENVS=all` routes every detected build environment
  through the sandboxed sidecar**, not just Android, without requiring an
  operator to enumerate every language by hand. Recommended when scanning
  source with unknown or untrusted build scripts (#255).

## [0.22.2] - 2026-08-29

### Fixed

- **The scan detail page's download button could stay disabled for the
  rest of a scan.** It gated on a REST snapshot of the scan's status,
  fetched once and never refreshed; a scan that finished emitting its
  early log lines before the page's own WebSocket connected left nothing
  to unstick it short of reloading the page. The live connection's own
  step now counts too (#227).
- **A super-admin's dashboard and project list failed outright past
  32,767 projects** - the point where a predicate built with one bind
  parameter per project id hits asyncpg's own ceiling. Mirrors the fix
  already shipped for the inventory view: ids go in as before below the
  ceiling, as a single array parameter above it (#228).
- **Two scans created at the same instant could have either one picked
  as "latest" at random.** Two succeeded scans of the same project can
  share an identical `created_at` (it is transaction time, not a
  per-row clock reading), and the tie-break behind it was a scan's
  random id. The scan's actual completion time now breaks the tie
  first (#228).
- **A component approval could reach a decided state without recording
  when.** A rejected component could then read as approved to a query
  that orders by decision time. A database constraint now requires the
  timestamp on every approved or rejected row; a migration backfills
  the rare existing gap before enforcing it (#228).
- **A failed write that a caller recovered from and retried on the same
  session could carry a stray audit-log entry into the next write**,
  recording it against whatever succeeded next rather than the change
  that was actually rolled back (#228).

### Added

- **The published OpenAPI spec now documents how to authenticate.**
  Every route reads its bearer token straight off the request rather
  than through a class FastAPI can introspect, so the served spec never
  had anything to base a security requirement on, despite nearly every
  operation needing one (#229).
- **A Postman collection and an official Python SDK**, both generated
  from the same OpenAPI spec as the Swagger UI and Redoc reference. The
  Postman collection ships from the docs site; four of its requests
  (login, create a project, trigger a scan, export the SBOM) carry real
  example values and pass their results to each other, so the
  walkthrough is runnable rather than only readable. The Python SDK
  ships as a wheel attached to this and future GitHub Releases (#230).

## [0.22.1] - 2026-08-24

### Fixed

- **The Hetzner demo overlay still defined the pre-split `worker` service**,
  so v0.22.0's worker-scan/worker-default split (S3) broke the merged
  compose file for that deployment (`invalid compose project`) and the
  deploy's failure-recovery path brought the stack back up without the
  overlay, dropping `DEMO_READ_ONLY` and the worker memory/CPU caps.
  `docker-compose.demo.yml` now defines both services, split from the same
  combined budget the single `worker` service used before v0.22.0.

## [0.22.0] - 2026-08-24

153 commits landed since v0.21.0. Most of this release is two tracks that ran
in parallel: a configurability track that turns things the portal used to
decide unilaterally (build-gate thresholds, who can accept a risk, who gets
notified, whether a component needs sign-off before it ships) into settings an
organization writes, and a concurrency and scaling track that makes the worker
fleet, the database and the search paths behave under real load rather than
under a demo seed. A security review alongside both found and closed an open
redirect on the OAuth callback and moved API-key hashing off bcrypt.

### Added

- **A deployment's own identity provider can sign people in.** A third OAuth
  provider, `oidc`, configured by issuer rather than pinned like GitHub and
  Google: an operator supplies an issuer, a client id and a secret, and the
  portal reads the rest from the issuer's discovery document. Discovery is
  checked against the issuer and requires HTTPS, and an address is only
  trusted to match an existing account once the provider has vouched for it
  as verified - a security review found and closed both gaps before this
  shipped. `OIDC_EMAIL_CLAIM` was proposed and removed in the same review:
  once the verified-address rule was in place, a deployment that used it to
  read the address from a non-`email` claim could no longer connect any of
  its own existing users, which made the setting unsafe rather than merely
  unused (#163).
- **Groups from the identity provider can decide the grade a new member
  gets**, through a map an operator writes. Sign-in used to make every
  arriving person the administrator of a personal team, which is right for a
  demo and wrong for a deployment where a whole company signs in. Left unset,
  nothing changes; `super_admin` cannot be granted through the map even if an
  operator writes it there, since an external directory should not be able to
  mint one (#164).
- **A read-only `viewer` grade.** Every account below `developer` used to be
  unable to look at anything; `developer` itself carries scan execution,
  every write and the source tree along with ordinary reads. `viewer` opens
  47 read routes (projects, components, vulnerabilities, licences,
  obligations, SBOMs, reports, search, the approval queue, licence policy)
  while leaving the source tree, credentials, the audit log and every write
  where they were. A membership can hold it, the admin UI can assign it, and
  every screen that offers a write action now checks rank instead of asking
  "is this exactly developer", so a viewer sees the same buttons a viewer
  should (#157, #158, #161, #162).
- **The build gate can answer to a policy row instead of only the
  environment.** `gate_policies` scopes the way licence policy already does -
  an organization default with optional per-team overrides, every column
  nullable and NULL meaning "not decided here" - with a read/write API and an
  effective view naming which scope a value came from. The policy screen
  gained the controls to edit it, with an explicit override switch per field
  so an empty threshold ("follow the organization") and a threshold of zero
  ("block on any score") stay distinguishable. A deployment with no rows
  evaluates exactly as before (#165, #166, #167).
- **A risk acceptance can require a second person.** An organization can name
  which vulnerability statuses (suppressed, not-affected) need one: the first
  analyst's transition opens a request instead of applying it, a different
  team admin has to agree, and the requester cannot approve their own
  request. Covers the single-finding transition, the bulk transition and VEX
  import alike (#168).
- **An organization can rule on a component once instead of per project.**
  A package thirty projects depend on used to be reviewed thirty times with
  usually the same answer, because the question is about the component. The
  new organization-wide ruling fills the gap only where a project has not
  decided; a project's own approval still wins (#171).
- **A team can ask about a package before it is pulled into a build.** Off by
  default. Approvals used to exist only after a scan found something; this
  lets a team record an intake decision on a bare purl ahead of the scan, and
  a later scan's approval picks up the earlier answer instead of asking again
  (#175).
- **Projects can record who owns them and how they ship** - an owning unit, a
  contact, and a distribution model that decides which licence obligations
  bind - with a portfolio filter for both (#172).
- **API keys can be issued read-only.** New keys default to read-only; keys
  issued before this release keep their existing read-write scope so nothing
  already integrated stops working. A read-write key can be narrowed to
  read-only from the keys table; narrowing is one-way (#173).
- **A key can belong to a service account that outlives the person who issued
  it.** A personal key stops working the day its owner is deactivated, which
  is wrong for a year-old nightly build. A service account is a flagged row
  in the same user table (kept off every people-facing surface: login,
  password reset, OAuth matching, the user and team-member lists) so the auth
  path's "is the issuer active" question needs no branch for it (#174).
- **Obligation fulfilment can be recorded** - status, owner, due date and an
  evidence link per project per obligation - so "did anyone actually publish
  the notice" has an answer inside the portal instead of a spreadsheet beside
  it. Recording it never narrows the obligation list and never changes the
  generated NOTICE file (#176).
- **An administrator can add and remove people in batches**, from a CSV in the
  same shape the roster exports in. Every row runs through the same service
  the single-user API uses, so a batch cannot admit an account the one-at-a-
  time path would refuse. A deployment using its own identity provider can
  also close the door on unknown people entirely: `AUTH_SELF_REGISTRATION`
  and the auto-provisioning switch both have to be off to close it, since the
  hosted sign-up form is a second way in (#178).
- **A deployment can reuse a resolved principal for a short, configurable
  time** instead of rebuilding it from the database on every request. Off by
  default, clamped to five minutes, and the number a deployment sets is the
  contract: the longest a demotion or deactivation can go unfelt (#179).
- **An organization can say who else hears about a notification** - by kind,
  minimum severity and project - on top of whoever a notification was
  produced for. With no rules written, delivery is unchanged (#182).
- **Operational metrics are published at `/metrics`** in Prometheus text
  format, off by default, and off answers 404 rather than 403 so an outsider
  cannot tell the endpoint exists. Seven series ship, each held to a
  fixture so a new one cannot be added without a deliberate decision that
  it is safe to expose; nothing identifies a project, package or person
  (#183). Two more arrived later behind their own toggle: live broker
  queue-backlog length and the age of the oldest still-queued scan, which
  tell a growing backlog apart from a busy-but-circulating one (#204).
- **A generic outbound webhook posts events worth a ticket to a URL an
  organization owns.** Replaces Jira settings that were placeholders nothing
  read. Off by default; an organization's own adapter turns the structured
  event into a ticket in whatever tracker it runs (#184).
- **The audit trail can be handed to a log collector.** A beat task posts
  batches to a configured URL every five minutes and only advances past a
  batch once the collector accepts it, so a failed delivery stalls visibly
  instead of leaving a silent gap (#186).
- **A project can scan itself on a schedule** - its own cadence, or an
  organization-wide default - through the same capacity and concurrency
  guards a webhook-triggered scan goes through. A schedule-triggered scan
  notifies the owning team on completion, since nobody else is watching for
  it (#188).
- **The project portfolio and per-project licences can be exported in bulk**
  as CSV, reusing the same list service and filters the screens use so the
  file and the page cannot disagree (#189).
- **An organization can add its own preface and footer to the generated
  NOTICE file**, per format (text/markdown/html). With nothing written, the
  document renders exactly as it always has (#190).
- **The vulnerability report can carry an organization's own header and
  column selection** instead of the built-in defaults, with the columns also
  overridable per request (#191).
- **`UVICORN_WORKERS` is a real runtime knob** rather than baked into the
  backend image's start command, so every deployment shape (Compose, dev,
  demo, Helm) sets its own worker count without rebuilding anything (#193).
- **The Helm chart's backend gets a PodDisruptionBudget and
  topologySpreadConstraints**, closing a standing gap where draining one node
  could take every backend replica down at once (#194).
- **The Helm chart can autoscale workers on queue depth instead of only
  CPU**, through an optional KEDA-backed mode: cdxgen/Trivy pipelines are
  network- and disk-wait heavy, so CPU usage can read idle while the scan
  queue backs up. CPU-based autoscaling stays the default (#214).
- **The Celery worker fleet can subscribe separately to scan work and
  everything else**, in both the Helm chart and Compose, so an hour-long scan
  and a one-second alert no longer sit behind each other in the same queue.
  Both worker kinds subscribe to both queues by default until an operator
  narrows them, so a rolling upgrade drains whatever is left on the
  pre-split queue name first (#215).
- **Compose deployments get an install-time capacity formula and a
  queue-backlog alert.** Compose has no autoscaler, so the installation guide
  now derives a slot count from the box's resources, and a beat sweep pages
  Slack or Teams once either queue's backlog stays over threshold for a
  sustained window, with a cooldown against repeat pages (#216).
- **Six previously unbounded tables now age out on their own schedule** -
  expired refresh and password-reset tokens, and old notifications, webhook
  deliveries and report downloads - each on a daily Celery beat, reusing
  indexes the tables already had. The audit log is deliberately excluded and
  stays a manual, two-operator procedure; a new daily report says when that
  session is due (#217).
- **AI model and dataset licences are judged against how the model is
  actually used** - internal experimentation, shipped inside a product, or
  redistributed - rather than against one blanket rule, with the terms
  vendored as data so a scenario change changes the verdict without a
  re-upload. Advisory only: no build gate, no approval workflow (#92).
- **A 429 from the concurrency cap can estimate how long the wait is**, and a
  webhook delivery turned away for capacity or a full disk now retries
  automatically with backoff instead of waiting for an operator to redeliver
  it by hand (#223).
- **A source scan skips cdxgen entirely when nothing it depends on has
  changed** - same manifests and lockfiles, same scanner version, same
  scan-time configuration as the project's prior succeeded scan on the same
  ref - and reuses that scan's preserved SBOM instead of spending 5 to 30
  minutes regenerating an identical one. Vulnerability matching and licence
  classification always re-run against current data regardless (#225).
- **A scan now records what it actually looked at, and a new scan-detail
  section shows it.** A source scan records the manifests and lockfiles its
  fetched tree carried (`scans.input_manifests`); an ingested SBOM records
  what its generator claimed about itself (`scans.input_document`), read
  from the original uploaded bytes rather than TRUSCA's conversion of them.
  A new `GET /v1/scans/{scan_id}/provenance` route and scan-detail panel
  make both readable, so a scan that reports fewer components than expected
  can be answered - did it not find the package, or did it never see the
  file that declares it (#45, #46, #47).
- **A vendored-code match now reports how many files backed it.** Files
  matching one purl used to collapse into a component keyed on purl and
  version alone, which listed the same library more than once when its
  files disagreed about the version; the match now carries the file count
  instead (#59).
- **Every CVE id, purl and CVSS vector has a copy button that names what it
  copies**, and a CVE id links out to NVD and OSV. Two of those strings are
  monospace and truncate, so dragging to select used to hand you half a
  string with no sign it was half (#124).
- **The browser tab is named after the screen**, and the record on it, so two
  projects open side by side (or two entries in history) are distinguishable
  again (#105).
- **The approvals queue is addressable and names the requester.** Opening a
  row is a URL now instead of component state, so it survives a reload and
  can be shared; `?project=` scopes the queue to one project; the requester
  column resolves a name instead of eight characters of a user id (#119).
- **A new organization's dashboard says what to do next**, as a four-step
  checklist read from the server rather than a single static instruction,
  so "register a project" is no longer the entire onboarding experience
  (#135).
- **Vulnerabilities, components and the cross-project inventory can leave as
  a filtered CSV file**, matching exactly what the toolbar is showing, the
  way the audit log already could (#133).
- **The application shell gained a skip link, a keyboard-shortcut sheet, a
  combined profile menu, and sidebar count badges** on Scans and Approvals,
  so destinations carrying work waiting on a person no longer look identical
  to the ones that do not (#134).
- **Web fonts are served from this origin instead of a third-party CDN.**
  Every page load used to fetch Inter and JetBrains Mono from a font CDN,
  which fails outright on an air-gapped install and announces every user's
  browser to a third party on every load. The bundled files are the upstream
  OFL-1.1 releases, satisfying the licence's per-copy attribution requirement
  on their own (#122).
- **A webhook delivery records why it went unscanned**, replacing a single
  NULL that collapsed four different outcomes into one unanswerable question
  (#98).
- **The first-scan journey's empty states now say what is actually true and
  offer a way forward** - the recent-scans table, the scan list, the
  components table, the source viewer, the policy list and the integrations
  card all used to state a generic absence or blame a filter nobody had set
  (#140).
- **A scan's WebSocket streams say when they have stopped**, instead of
  showing a reconnect counter forever: the progress panel and the log panel
  each report their own state now, with a reason drawn from what the server
  actually sent rather than four largely incorrect close-code labels (#139,
  #143).
- **An unmatched route lands on a 404 inside the application shell** that
  names the address, instead of silently redirecting to `/login` as though
  the session had expired (#115).
- Helm chart: `env.extraEnv` and `env.extraEnvFrom` set any runtime variable
  the chart does not name, injected into backend, worker and beat. This is
  how a Helm install reaches OAuth sign-in, SMTP / Slack / Teams
  notifications, the vendored-code identification service and the Jira link,
  none of which were configurable there before (#81, #101).

### Changed

- **Minimum search query length raised from 2 to 3 characters.** A 2-character
  query bypasses the trigram indexes search relies on and falls back to a
  sequential scan; a 2-character partial match also hits the result cap
  without a useful answer (#212).
- **Component search (palette and full results page) narrowed to each
  project's latest scan**, matching how vulnerability and licence search
  already worked. Search cost used to grow with retained scan history - up
  to 30x - rather than with catalogue size. A component that only ever
  appeared in an older, superseded scan no longer surfaces in search (#218).
- **The search results page caps its total and facet counts** at 1,000
  matches instead of scanning the full match set on every keystroke. Below
  the cap counts stay exact; at or past it, the UI shows "N+" rather than a
  number it cannot vouch for (#219).
- **`api_keys.last_used_at` is now written at most once every 15 minutes**
  per key instead of on every authenticated request. The column's meaning
  changes accordingly, from "used at this exact instant" to "used within
  this interval," and the interval is documented in both guides (#209).
- **Postgres connection pool defaults lowered across every deployment
  shape** (prod and dev Compose, the demo overlay, Helm) so process count
  times pool size fits under Postgres' default `max_connections`. The old
  Helm defaults alone put the backend tier at 240 connections against a
  default ceiling of 100; a boot-time warning now fires when a deployment's
  declared shape still exceeds it (#192).
- Helm chart version and `appVersion` realigned with the portal at 0.21.0.
  They had drifted nine minor versions, so a default `helm install` ran an
  old portal unless the operator overrode `image.tag`. Bumping the chart is
  now a step in the release procedure (#81, #101).

### Fixed
- **The vulnerabilities table stopped at 100 findings, with no way to reach
  the rest.** A project with more than a hundred findings hid them silently.
  The list now pages the way the components table already did, and the
  licences and obligations grids, which had the identical defect, page the
  same way (#109, #111).
- **An organization with more than roughly 32,000 in-scope projects got a
  500 instead of a page.** The dashboard's scan-inventory query bound one
  parameter per project id, and the database driver refuses a statement past
  32,767 bound parameters. Past a configurable threshold the predicate now
  goes in as a single array parameter instead (#152).
- **The project detail header overflowed at 390px** on the Overview and
  Vulnerabilities tabs; the title block and the scan controls now stack
  instead of sharing a row below the small breakpoint (#222).
- **Five list screens lost their filters on reload**, and Back left the page
  instead of undoing the last change, because filter state lived in the
  component rather than the URL. A shared hook now keeps all five in the
  address bar (#125).
- **The API key create button was hidden from developers who could already
  issue a project-scoped key.** The button checked for team-admin-or-above;
  the backend has always allowed any team member to issue a key at that
  scope (#141).
- **OFL-1.1 (the SIL Open Font License) classified as `uncategorized`**
  instead of the weak-copyleft licence it is, so a component under it read
  as unknown everywhere in the UI. A licence-conflict rule for it exists now
  too (#150).
- **A Creative Commons NonCommercial licence normalised to plain CC-BY and
  passed the build gate.** The licence-name pattern let a wildcard swallow
  the `NonCommercial` and `NoDerivatives` clauses, so "Attribution-
  NonCommercial 4.0 International" stored, gated and displayed as an
  ordinary permissive licence. NC now classifies as forbidden and ND as
  conditional, and an unrecognised restricted variant fails closed instead
  of falling through to plain CC-BY (#50).
- **The conformance panel scored a dataset's governance as missing when the
  SBOM declared it correctly.** Registry v3 looks for the dataset cluster
  under CycloneDX's `data` field; the reader was still looking under
  `.componentData`, a name that only ever existed in the schema's type
  system, not in the JSON itself (#51).
- **The public webhook receiver could answer 500 instead of refusing
  cleanly.** `DISK_HARD_LIMIT_PCT` parsed through a bare `float()`, so a
  typo in that setting raised out of the disk guard on every delivery
  before a signature could even be checked; a value of `0` refused every
  scan with nothing in the log. The receiver also had no request body-size
  cap and no IP-keyed rate limit, the two bounds any surface that runs
  ahead of authentication needs. All three are fixed together, since they
  sit on the same unauthenticated code path (#64).
- **The OpenAPI schema did not describe the errors the API actually
  returns.** Every error leaves this API as an RFC 7807
  `application/problem+json` response, and the generated schema still
  declared only FastAPI's default 422 shape, with no 401, 403 or 404 on
  routes that return them - so a generated client trusted a shape the
  server never sent. The schema now states the real content type and the
  status codes each route can answer with (#77).
- **Every authenticated request loaded the current user in two statements
  instead of one**, a separate query per request for something a single
  join already answers. Folding it into one `LEFT JOIN` lowers the
  authenticated-read query budget by one statement on every request in the
  product (#208).
- **A single forbidden licence on a tri-licensed component was not
  re-checked against the licence registry.** cdxgen recorded only the first
  matching classifier where the registry knows all of them (e.g. a
  GPL/LGPL/MPL package, which should read conditional, not forbidden); the
  scan now re-checks the one case that matters instead of every component
  (#95).
- **The password-reset cooldown was compared against the application's
  clock instead of the database's**, which measured up to 2.8 seconds behind
  it on the same machine - enough to leave a configured cooldown window
  running longer than configured (#97).
- **AI-model license conformance missed a model that is an SBOM's own
  subject** rather than a dependency it names - an ML-BOM published about a
  model, not about the job that built it, read as containing no models at
  all, silently dropping the entire 51-check baseline (#93).
- **Plural translation keys were never resolved.** The build gate's
  known-malicious-package message used the old i18next v3 `_plural` suffix,
  which v4 does not read, so a build blocked on five packages reported one.
  `i18n:check` now rejects the old suffix, and a runtime contract test
  resolves every plural key in both locales (#102).
- **The five severity tiers had more than one Korean name.** Critical read
  as 치명 in five places and 심각 in one, and the portfolio legend was left
  in English in one more; a contract test now holds every place that names
  the whole scale to one vocabulary (#103).
- **Backend error messages reached Korean sessions in English** on 47 call
  sites that read the API's (always-English) RFC 7807 detail text directly
  instead of the translation keys sitting unused beside them. A shared
  `problemMessage()` helper now classifies the failure and translates the
  class, falling back to the backend's text only where nothing on the
  frontend knows better (#104, #106).
- **Counts rendered as raw digit strings with no thousands separator**, and
  a quiet trend window printed "+0 −0" as though two changes had cancelled
  out rather than nothing having happened (#130).
- **The audit log printed a raw UTC instant with no timezone**, while every
  other timestamp on the page rendered in the browser's own zone - the same
  moment could read as two different times with nothing to explain the gap.
  One shared formatter now names its zone everywhere (#126).
- **`?release=` on the releases list and detail endpoints answered whether a
  version label existed even for a team that could not read it** (403 for an
  existing label, 404 for a nonexistent one - a status-code oracle). The
  lookup is now team-scoped before it answers either way (#146).
- **A very large `page` query parameter caused a 500 instead of a 422.**
  Every listing endpoint bounded `page` below but not above, so an
  unbounded value reached Postgres as an out-of-range integer. All 19
  affected endpoints now share one ceiling (#74).
- **A NUL byte in a text field caused a 500 instead of a 422** on any
  endpoint that stores a caller-supplied string, since Postgres rejects
  `U+0000` in `text` and `jsonb` after validation has already passed (#79).
- **The Flagged-only component filter did nothing.** The toggle wrote a
  query parameter the fetch hook never declared, so the list never
  narrowed; a table width regression from the same period is also fixed
  (#70).
- **Backups failed with a permission error on the non-root worker.** Moving
  the worker to a fixed uid left the backups directory unclaimed by it on
  bind-mounted dev deployments; it now uses a named volume owned by that
  uid (#70).
- **A scan running past the Celery broker's visibility timeout could be
  redelivered to a second worker while the first was still running it**,
  letting one scan occupy two slots. The timeout is now derived from the
  scan hard time limit plus a margin instead of using Redis's 3600-second
  default (#195). Operators may see scan failures that this had previously
  been masking as a silent retry.
- **The reachability follow-up task had no time limit at all** and could pin
  a worker slot indefinitely on a hang outside its already-bounded
  `govulncheck` subprocess call (#196).
- **bcrypt password verification ran on the request event loop**, blocking
  every other request on that worker for its full ~213ms during every login
  and API-key check. Verification now offloads to a thread (#197).
- **SBOM export blocked the event loop while building and serializing large
  documents**, serializing every other request behind it; all four export
  formats now offload the same way the PDF/XLSX reports already did (#199).
- **The dashboard's action-queue recount issued one query per project**
  instead of batching every affected scan into one, and `/summary` carried
  no rate limit while the other dashboard routes did (#200).
- **The per-user WebSocket connection cap lived in a process-local dict**, so
  whether a second tab evicted the first depended on which worker process or
  pod the sockets happened to land on. It is now Redis-backed and exact
  across the deployment, and a new global cap refuses connections outright
  once the whole system is saturated instead of evicting an unrelated user
  (#202).
- **A worker pod's termination grace period (30s Kubernetes default, 10s
  Compose default) was far shorter than a scan can legitimately run**, so
  scaling the worker pool down under a growing queue could restart in-flight
  scans from zero and make the queue longer instead of shorter. Both now
  match the scan hard time limit with a margin, and a `preStop` hook
  triggers Celery's warm shutdown (#211).
- **A Helm-rendered Secret silently derived `API_KEY_HMAC_SECRET` from
  `secretKey` when left blank**, so the fail-closed check meant to catch a
  missing HMAC secret never fired, reopening the exact exposure the HMAC
  migration (#221) closed: a leaked `SECRET_KEY` would also expose API-key
  hashing. The chart now requires the value explicitly, the same as
  `secretKey` (#226).
- Container images stopped publishing a movable `latest` tag alongside the
  versioned one; `docker/metadata-action`'s `flavor: latest=auto` appended it
  automatically regardless of the workflow's own tag list (#72).

### Security
- **New API keys are hashed with HMAC-SHA256 instead of bcrypt.** An API-key
  secret is a 192-bit random value, not a human-chosen password, so bcrypt's
  deliberate slowness defends nothing while still costing roughly 213ms of
  CPU per verification. Keys issued before this release keep authenticating
  against their existing bcrypt hash; a new admin endpoint reports how many
  are still on the legacy format. The change reopened a timing oracle where
  response time alone could reveal whether a guessed key prefix existed on
  the legacy format, which is closed by padding every verification branch to
  a shared wall-clock floor until the fleet has migrated (#221). A related
  Helm gap that could silently reuse `SECRET_KEY` for this secret is fixed
  in the same release (#226, see Fixed).
- **The `redirect_after` parameter on the public OAuth authorize endpoint was
  never validated**, so a link could carry an absolute URL that travelled
  into the signed OAuth state and out of the callback as a redirect in the
  same response that sets the refresh cookie - an open redirect at the exact
  moment a lookalike page is most convincing. It is now checked when the
  state is minted and again when it is read, resolved only against the
  frontend's own origin. A related, lower-severity gap on the frontend's own
  post-login redirect is fixed in the same change (#120).
- **`/auth/reset-password` had no rate limit and verified every candidate
  reset token with a synchronous bcrypt loop** (up to 256 candidates) inside
  the request handler, letting an unauthenticated caller stall the whole
  worker for tens of seconds. It now shares login's 5/minute/IP default and
  runs the verify loop as one offloaded unit of work (#198).
- **Every production container now runs as its own unprivileged user
  instead of root.** The frontend image runs as `nginx`, the worker image
  (the last one still running as root, because its bundled SCA toolchain
  caches under whoever's `HOME` runs it) now runs as `trustedoss` with every
  cache path pinned under its home directory, and the Helm chart gives
  backend, frontend, worker, beat and redis a real pod and container
  security context: a pinned non-root uid, every capability dropped, no
  privilege escalation, and a sealed root filesystem where the image allows
  it (#62, #63, #65).
- **Worker image: patched several CVEs surfaced by scheduled image
  scanning** - cosign bumped past a `golang.org/x/net` denial-of-service
  (#91); two Go stdlib waves patched across the bundled toolchain, with a
  documented reach analysis for the two prebuilt binaries (cosign, the
  Docker CLI) that ship their own Go runtime ahead of upstream fixing it
  (#116, #127); and cdxgen's bundled `jsonata` (arbitrary code execution via
  crafted expressions) and `tar` (path-length denial-of-service) patched via
  the existing npm-pack-swap pattern (#201).

## [0.21.0] — 2026-08-10

### Added
- **Every CycloneDX SBOM is measured against the 2026 minimum elements.** The
  2026 Minimum Elements for a Software Bill of Materials (v2.1, 2026-07-29,
  CISA/NSA/FBI with fifteen international partners) replace the NTIA elements of
  2021 and apply to all software, so the baseline carries no condition — 17 data
  fields and 6 practices, 23 checks. Every one is advisory: the guidance accepts
  an explicit statement that a value is unknown in place of the value, so
  promoting a check would fail an SBOM for a value it may legitimately not have.
  Four practices describe how an organisation operates and carry a review note
  rather than a score. The regulatory crosswalk's US framework moves onto these
  elements, and its rollup now states `failed` instead of leaving it as the
  remainder — the four counters partition the total.
- **A project can declare the licence it ships under, and every dependency is
  judged against it.** A conflict exists only relative to an outbound licence, so
  the policy axis could not express one. Declaring it turns on a verdict column,
  filter and summary on the Compliance grid, with the reasoning shown in the
  drawer. No declaration means no verdicts — an empty column is not a clean
  result. Migration 0050.
- **Exports state their generation context, author and tool.** The lifecycle
  phase follows from the scan kind (source manifests pre-build, a built image
  post-build); an ingested supplier document is re-exported with no phase, since
  converting someone else's document does not make us its author. `SBOM_AUTHOR`
  declares the author and the field is omitted when unset rather than filled with
  a placeholder. The document also states once whether an empty field is unknown
  or withheld.
- **The conformance panel shows the CycloneDX fragment that would satisfy a
  missing element**, for both baselines, in a fold-away block. Guidance is no
  longer attached to rows that already pass.

### Fixed
- **A pull request's scan described the base branch.** Every source scan ran
  `git clone --depth 1`, which takes the remote's default branch; the ref
  travelled alongside as a retention key only. A PR that added a vulnerable
  dependency passed, and a critical CVE on `main` blocked PRs that had nothing to
  do with it. The worker now fetches and checks out the ref. A ref that has since
  vanished falls back to the default branch and records `metadata.ref_fallback`,
  because a verdict from a substituted target must be distinguishable.
- **A supplier SBOM of distro packages reported zero vulnerabilities.** Trivy
  picks a distro advisory database from an `operating-system` component, not from
  package PURLs, so an SBOM listing every rpm on an image — each PURL well formed
  — matched nothing. Measured on Trivy 0.71.2: 0 findings without the component,
  306 with it; SPDX behaves the same way. The distro is now inferred from the
  packages and an enriched copy is scanned; the upload itself is never edited,
  since it backs the conformance verdict and the signature bundle. An
  unrecognised distro contributes nothing and the document is scanned as it
  arrived.
- **Distro findings were dropped between the scanner and the database.** Trivy
  labels an OS-package result with the distro (`centos`, `alpine`), which no PURL
  reconstruction maps, so findings it had matched were discarded as "no purl".
  The PURL Trivy attaches to each finding is now used as a fallback;
  reconstruction stays first, so every ecosystem that already matched is
  unaffected.
- **Container scans recorded every package as an Alpine package.** Persistence
  hardcoded `pkg:apk/{name}@{version}` and package type `apk` for every image, so
  a Rocky image's rpms, a Debian image's debs and `pip` inside a Python image were
  all inventoried under the wrong ecosystem. No finding was lost, so the counts
  were right and only the identity was wrong. The ecosystem the scanner reports
  is now recorded. **Existing rows are not rewritten**: re-scan an rpm or deb
  image to correct its inventory, and see the upgrade guide — a re-scan resets
  that project's vulnerability triage, because verdicts are keyed on the
  component and the corrected package is a different component. Deployments that
  only scanned Alpine images are unaffected.
- **A CI container scan could never succeed.** Neither CI client could send
  `image_ref`, which the worker requires, so every container scan they triggered
  queued, waited for a worker and died there. The action and the GitLab template
  now take the image, and the request is rejected at trigger time instead.
- **A second CI run against the same ref failed the build.** The portal allows
  one active scan per (project, ref) and answered a second trigger with 409,
  which all three CI clients treated as fatal — so GitHub's own recommended
  `cancel-in-progress` workflow broke itself. The 409 now names the scan holding
  the ref and the clients wait on it. Polling survives a blip rather than
  discarding a nearly-finished scan, and 429 honours `Retry-After`.
- **A push could go unscanned while the delivery log said otherwise.** A delivery
  skipped because the ref already had a running scan was reported as
  `duplicate` — this API's word for a replayed delivery — so an operator saw a
  commit as handled when nothing had scanned it. It now says
  `skipped_active_scan`. That path also answered 500, which the Git host retried.
  Pull-request events carry no top-level ref, so webhook-triggered PR scans were
  stored ref-less; they are now keyed `pr-N` / `mr-N` like the CI clients' scans.
- **The webhook receiver ignored the capacity guards.** It built scan rows
  directly, counting against neither the team concurrency cap nor the disk limit.
  It now asks before recording the delivery, so a redelivery recovers once
  capacity frees up.
- **Nested components were invisible.** CycloneDX lets a component declare its
  own `components` array — a bundled dependency, an image layer's packages — and
  only the top level was read. Persistence dropped the nested entries,
  conformance scored a denominator without them, and a Trivy finding against one
  was resolved by PURL to a row that was never written, so the finding vanished
  with nothing logged.
- **File entries were counted against the package fields.** A file carries no
  package version and no PURL type, so counting file entries against those fields
  reported a document as short on what its entries cannot hold. Files are now
  asked for the identifier they can carry, and the coverage detail names how many
  components were measured against how many were left out.
- **The signed document said less than the exported one.** The 2026
  minimum-element statements landed on the export route first, leaving the SBOM a
  consumer actually receives as the silent one. Both routes now apply them, and
  on the scan route the stamp runs before the artifact is persisted, so the bytes
  that are signed are the bytes that carry the statements.
- **Every SBOM asserted a release that does not exist.** The builder version
  defaulted to `2.3.0-dev` and no deployment set `TRUSTEDOSS_VERSION`, so SLSA
  provenance, the About screen and every SBOM stated a placeholder. Release images
  inject the tag at build time and the default is now `unknown`.
- **The regulatory crosswalk's disclaimer named the wrong product.** The
  conformance panel's legal notice began "BomLens does not certify or determine
  compliance…" — the name of the sibling project the crosswalk data was
  vendored from, not the product showing it. It now reads TRUSCA, in both
  languages. Installations on 0.20.x still show the old wording until they
  upgrade. Attribution to the upstream project is unaffected and stays where
  Apache-2.0 §4(d) puts it, in `THIRD_PARTY_NOTICES.md`; the changes made to
  the vendored files are now stated there per §4(b). A guard test walks every
  vendored data file so a re-copy cannot quietly bring the name back, and the
  disclaimer the API serves is now pinned to the copy quoted in the SBOM upload
  guide — the two had drifted while the guide promised they matched.
- **The CI guides promised things that were not there.** The webhook secret
  recipe imported a function that does not exist, the Jenkins quickstart graded a
  failed scan against an older one, and three guides pointed at a portal page and
  API key scopes that do not exist. The action now emits the `epss-gate-count`
  output its guide documented, with a contract test so the two cannot drift
  again.

### Changed
- **The conformance panel leads with what blocks the SBOM** — mandatory
  failures, advisory shortfalls, rows needing a person — and gives each baseline
  its own section, so 23 advisory rows cannot push the mandatory checks down the
  page.

## [0.20.1] — 2026-08-05

### Added
- **Known-malicious packages now fail the build — this axis is on by default.**
  A build that passed on 0.20.0 can fail on 0.20.1 if it carries a package named
  by an OSV `MAL-` advisory. Severity is not weighed: the published artifact is
  the attack, so there is no version to upgrade to. An upstream false positive
  would otherwise stop every build until the advisory is retracted, so a licence
  policy can carry `malicious_exceptions` — package, reason, and a **required**
  expiry capped at `MALICIOUS_WAIVE_MAX_DAYS` (30). Set
  `GATE_MALICIOUS_ENABLED=false` to keep the verdicts visible without blocking.
- **A weekly re-check catches packages flagged after they shipped.** Verdicts
  previously came only from a scan, and nothing re-scans an old release, so a
  package named by an advisory published after the last scan stayed unflagged.
  The beat runs Sundays 02:40 UTC and alerts the teams whose latest scan carries
  it. Rebuilding the snapshot from OSV is a separate switch
  (`MALICIOUS_REFRESH_ENABLED`, off by default); the re-check itself needs no
  network, so an air-gapped install still picks up a newer snapshot from an
  upgrade. `admin/health` gains a status panel.
- **A version label is a permanent address for a snapshot.** Two succeeded scans
  could carry the same `metadata.release`, so "which snapshot is 4.0?" had no
  answer; a labelled scan now supersedes earlier ones with the same label, and
  the displaced scan is exempt from the reclaim sweep. `?release=4.0` works on
  the releases list and on fourteen detail endpoints, so `/notice?release=4.0`
  no longer requires knowing a scan UUID. Migration 0046.

### Changed
- **Scans serialize per branch, not per project.** Pushing to `main` and
  `release/1.x` at once made one CI job wait on the other or take a 409 it could
  do nothing about, though the branches write disjoint snapshots. Migration 0047
  rebuilds the index on `(project_id, ref)`.
- **The gate reads current state from the project's main line**, not from
  whichever scan finished last, so a feature-branch scan no longer decides
  whether `main` is passing.

### Removed
- **The documentation comparison page**, along with its sidebar entry and the
  thirteen links that pointed at it. Each link now goes to the page that holds
  the detail it was reaching for: the roadmap, the introduction, `data-sources`,
  or the licence classification table in `components-and-licenses`.

### Fixed
- **The malicious verdict is decided from the PURL**, not a sibling field an SBOM
  upload could set — that let an upload clear the shared catalogue's flags for
  every project.
- **NOTICE downloads honour the `?scan_id=` release pin** instead of always
  serving the newest scan.
- **The notification inbox showed a raw translation key for `vuln_sla_breach`.**
  The kind shipped without a frontend label; the shared vocabulary fixture that
  exists to catch that was wired to only one side, and now guards both.
- **The demo deploy path.** `upgrade.sh` hardcoded `-f docker-compose.yml` and
  dropped the demo overlay, the required backup did not run with the stack down,
  and a failed deploy left the service stopped.
- Korean term spellings and one mistranslation; the release list is now called
  "releases", with "version" reserved for the label.

### Security
- **`cryptography` bumped for CVE-2026-69247.**
- **`brace-expansion` and `ip-address` pinned to fixed versions in the worker
  image**, sweeping npm's own dependency tree rather than only cdxgen's.

## [0.20.0] — 2026-08-03

No itemised entry was written when this release was tagged, and the notable
change below is the only one recovered. Later releases are itemised in full.

### Security
- **Worker image: replace npm's bundled brace-expansion 5.0.7 (CVE-2026-14257,
  HIGH ReDoS) with 5.0.8.** npm is a build-time tool in the worker image and is
  never invoked at runtime, so the flaw was not runtime-reachable, but 5.0.8 is
  a drop-in patch — the Dockerfile now npm-pack-swaps the vulnerable copy (same
  pattern as the cdxgen tar override) rather than blanket-ignoring the CVE ID.
  Clears the `image-scan (worker)` gate that was blocking all PRs.

## [0.19.2] — 2026-07-25

### Fixed
- **Maven / Java scans now report their vulnerabilities (they previously showed
  zero).** Trivy names Maven packages `groupId:artifactId` (a colon), but the
  finding→component matcher percent-encoded that colon (`group%3Aartifact`)
  instead of splitting it onto the PURL namespace path (`group/artifact`), and
  it compared PURLs by exact string — so a stored
  `pkg:maven/g/a@v?type=jar` never matched a Trivy finding. Every Maven/Java
  component was skipped as "unknown component", so a Java SBOM (uploaded or
  source-scanned) reported **0 CVEs even when Trivy found many** (e.g. a Spring
  Boot SBOM: Trivy found 65, TRUSCA persisted 0). `_build_purl` now splits the
  Maven colon, and the component lookup ignores PURL qualifiers (`?type=jar`
  etc. — Trivy omits them, cdxgen/BomLens include them, and they are not part of
  the package identity). The `_build_purl` unit fixtures now use the real
  `groupId:artifactId` form (hardening rule **H-1**: a synthetic slash fixture
  had masked this). Follow-up: a Maven persist-boundary integration test with a
  real Trivy fixture + `?type=jar` component.

## [0.19.1] — 2026-07-25

### Fixed
- **SBOM ingest no longer 500s on a fresh deployment.** The backend runs as a
  non-root user while the worker runs as root, and they share the `/workspace`
  volume; on a fresh named volume the mount root is `root:root 0755`, so the
  backend could not create the `sbom-ingest/…` subtree and `POST
  /v1/projects/{id}/sbom-ingest` failed with `PermissionError` (HTTP 500). A new
  worker-boot hook (`tasks.workspace_prep`, a `worker_ready` handler) makes the
  shared workspace writable (mode 1777) once at boot. Source scans were
  unaffected (the root worker did that writing); SBOM upload — the
  BomLens → TRUSCA path — now works out of the box.
- **`install.sh` succeeds on a host with fewer than 4 CPUs.** The base compose
  capped the worker at `cpus: "4.0"`; under Compose V2 a limit greater than the
  host's online CPU count is a HARD error at `up`, so the bring-up failed on a
  2-vCPU box (the CAX11 / CX23 runbook default). The cap is now
  `${WORKER_CPU_LIMIT:-4.0}` and `install.sh` clamps it to the host CPU count
  (hosts with ≥ 4 CPUs keep 4.0).

### Changed
- **Login demo hint points sandbox users to the right account.** The public-demo
  login helper now notes that running a live scan or uploading a BomLens SBOM
  requires signing in as `dev@demo.trustedoss.dev` — the Demo Sandbox project is
  team-scoped and not visible to the browse-oriented `frontend-admin` account the
  hint advertises.

## [0.19.0] — 2026-07-25

### Added
- **Public demo sandbox scans (opt-in).** The read-only demo can now let
  visitors run a bounded live scan or upload a BomLens-produced SBOM, without
  giving up the read-only safety boundary. Enabled only when both
  `DEMO_READ_ONLY` and the new `DEMO_ALLOW_SANDBOX_SCANS` are set — off by
  default, so a normal read-only demo stays fully locked. When on, exactly two
  write paths open (`POST /v1/projects/{id}/scans`, `POST
  /v1/projects/{id}/sbom-ingest`) and only against the seeded "Demo Sandbox"
  project; every other write, other project, and non-source scan kind is
  refused (403 / 422). Container scans are blocked in the sandbox (no
  unguarded image-ref pull). The deployment ships tight bounds — 10 MiB source
  input, 10 MiB / 3000-component SBOM ingest, one scan at a time, scancode off,
  a 2 GiB worker memory cap so a runaway scan is OOM-killed inside its
  container rather than taking down the host — and boot fails fast if the
  carve-out is on without those bounds applied. The SPA re-enables the scan /
  upload affordances on the sandbox project only, with guidance to run larger
  projects in BomLens and upload the SBOM, and a shared-public-sandbox warning.
  Runs comfortably on a small (~4 GB) box; see the Hetzner CAX11 path in the
  operator runbook. Producer-Reviewer: security review sign-off after two
  rounds (SSRF, sandbox confinement, resource bounds).

### Changed
- **First-time visitors default to English.** The language detector no longer
  falls back to the browser locale for a fresh visitor — with no stored choice
  it uses the app's English default (English is the product's primary language
  and the public demo serves a global audience). A visitor's explicit language
  pick is still remembered via `localStorage` and restored on return.

### Fixed
- **Demo read-only lock is now actually enforced in the deployed stack.**
  `DEMO_READ_ONLY` / `DEMO_ALLOW_SANDBOX_SCANS` were set in `.env` but never
  passed into the backend container, so the backend read them as unset and the
  public read-only boundary silently stayed OFF (writes were accepted). The
  demo overlay now plumbs both flags into backend/worker/beat; verified via
  `/health` (`demo_read_only:true`) and a write probe (`POST` → 403).
- **Traefik works on Docker Engine 29+.** Docker 29 raised its minimum
  negotiable API to 1.44 and rejects Traefik v3.2.1's legacy 1.24 client, so
  the docker provider discovered no routers and served its self-signed default
  cert (no Let's Encrypt, no routing). Bumped Traefik to v3.6.1, which
  negotiates the API version with the daemon.
- **`security-headers` middleware is now defined.** The backend and frontend
  routers referenced `security-headers@file`, but no file provider or dynamic
  config ever defined it — so once the docker provider worked (Traefik ≥ v3.6.1
  on Docker 29) both routers became invalid. It is now defined via
  docker-provider labels and referenced as `@docker` (HSTS, nosniff,
  frame-deny, referrer-policy).

## [0.18.0] — 2026-07-25

### Added
- **Vulnerability SLA / aging tracking.** Findings now carry a project-level
  first-detection timestamp (`first_detected_at`) that survives re-scans,
  re-matches and container re-scans — the remediation clock no longer resets
  every scan. Severity-based SLA due dates ride on top
  (`VULN_SLA_DAYS_CRITICAL/HIGH/MEDIUM/LOW`, defaults 7/30/90/180; info and
  unknown severities carry no SLA), served on the vulnerability list and
  detail APIs as `first_detected_at` / `sla_due_date` / `sla_status`
  (overdue · imminent · ok) with an `sla=` filter and `sort=sla_due`. The
  Vulnerabilities tab gains an "SLA due" column, an inline SLA filter and a
  drawer chip with the first-detected line. A daily sweep
  (`trustedoss.vuln_sla_sweep`, toggle `VULN_SLA_ALERTS_ENABLED`, default on)
  notifies the owning team once per project when open findings cross their
  due date (notification kind `vuln_sla_breach`, per-user preferences
  respected). Closes completeness-master-plan §9 X1 — the aging /
  "remediate without undue delay" gap the CRA compliance mapping had to
  flag as a limitation.
- **SBOM conformance: regulatory field checks + crosswalk.** Uploaded-SBOM
  conformance scoring gains five advisory per-component field checks named by
  the regulatory baselines (BSI TR-03183-2 for the EU CRA, the NTIA minimum
  elements): SHA-512 checksum, component creator, component filename, source /
  distribution URI, and delivered-file properties coverage. They are
  verdict-neutral — they describe how well the SBOM would answer a regulator
  and never move the pass / warn / fail badge (`SBOM_CONFORMANCE_FIELD_MIN_PCT`,
  default 80, tunes the coverage bar). The conformance response and panel gain
  a regulatory crosswalk: per-framework rollups (BSI TR-03183-2, NTIA, EU AI
  Act Annex IV, the Korean AI Framework Act) showing which mapped requirements
  are present, a gap, or human-review-only — explicitly a
  documentation-preparation aid with the vendored disclaimer, not a compliance
  determination. Coverage checks also stop failing zero-package SBOMs
  ("no packages to measure" instead of "0%"), and dataset (`type: "data"`)
  components no longer count against package-only fields such as PURL
  coverage. Vendored from and parity with BomLens (sktelecom/sbom-tools #454,
  #457, #462).
- **Version currency: "behind latest patch" component signal.** A sibling of
  the EOL flag, answering a different question — not "is this release line
  dead?" but "is this version behind the newest patch of its (still-supported)
  release line?". It reuses the vendored endoflife.date snapshot (each cycle
  carries its newest patch), so it is fully offline — no new network at scan
  time. Components in the Components tab and drawer gain an "Outdated" badge
  (a lower-urgency amber-below tone than EOL) with the latest patch version, an
  "Outdated only" filter (`?outdated=true`), and a project-Overview "N
  outdated" chip. The deps.dev "absolute-newest across the ecosystem / N
  releases behind" enrichment is a separate opt-in egress path, not included
  here.
- **Vulnerabilities "Group by upgrade" view.** The project Vulnerabilities tab
  gains a Flat ⇄ By upgrade toggle. "By upgrade" replaces the flat finding list
  with the whole-project set of remediation clusters — each the *minimum safe
  upgrade* for one component (the semver-maximum of its open findings' fix
  versions), showing "Upgrade {component} {from} → {to}" and how many findings
  it resolves, expandable to the findings themselves (which still open the same
  drawer). Components whose open findings have no published fix are grouped
  under "No upgrade available" rather than given a misleading partial bump.
  Served by `GET /v1/projects/{id}/vulnerabilities/upgrade-clusters`, which
  reuses the existing upgrade-recommendation engine and the build gate's own
  open-status set, so the cluster counts stay in lock-step with the gate.

### Documentation
- **Wave 7 documentation parity.** New pages, EN + KO: a **Triage** guide
  (`user-guide/triage.md`) that consolidates how a finding flows across VEX
  vulnerability triage, component approval, and the build gate — and makes
  explicit that a rejected component approval does not gate the build; an
  **Analysis types** reference (`reference/analysis-types.md`) matrix of source
  SBOM scan / container scan / policy gate / (planned) reachability; a
  **Best practices** category (scan frequency, policy design, team structure,
  upgrade cadence); and an **FAQ** (`reference/faq.md`) link hub. Also wired the
  previously-unlisted v0.13.1 and v0.14.0 release notes into the sidebar.

### Added
- **Container scans surface base-image OS end-of-life (EOSL).** Trivy reports
  whether a scanned image's base OS release is past its end-of-service-life —
  it no longer receives upstream security fixes, so newly disclosed CVEs will
  never be patched on it, a risk that no individual package CVE captures. We
  now persist that OS family/release + EOSL flag (into `scan_metadata`, no
  migration) and show an "OS end-of-life" panel on the scan detail page when
  the release is EOL. The verdict comes from Trivy's bundled database (no extra
  network), so a stale database may not yet flag a recently retired release.

### Fixed
- **Dynamic-scan sidecar now targets the git-clone root.** With
  `SCAN_EXECUTOR=local_docker`, an Android scan of a **git** repository routed
  to the sidecar but read the compileSdk and ran the build from the outer
  workspace directory instead of the clone root a level below — so it picked
  the default SDK image and scanned an empty directory. (Language detection is
  non-recursive and the sidecar targets a single directory, unlike the default
  in-process cdxgen which recurses.) The scan executor now carries the resolved
  project root and the sidecar uses it. Only affects the opt-in `local_docker`
  executor; the default in-process path was unaffected.

### Added
- **License enrichment now covers RubyGems and NuGet, and is air-gap gated
  (`LICENSE_FETCH_ENABLED`).** When cdxgen emits a component with no SPDX
  license — the common case for a bare `requirements.txt`, `Gemfile`, or
  `.csproj` — the pipeline looks the license up in the component's public
  registry and records it as a *concluded* finding, pulling the "unknown"
  license ratio down. Gem (`rubygems.org`) and NuGet (`api.nuget.org`)
  fetchers join the existing PyPI / Maven / crates.io / pkg.go.dev set, so
  Ruby and .NET dependencies are no longer 100% unknown. The lookup was
  previously unconditional scan-time egress; it now respects an
  `LICENSE_FETCH_ENABLED` flag (default **on** — only a package name+version
  leaves the network) so an air-gapped deployment sets it `false` to skip the
  fetch cleanly instead of paying a per-component network timeout.
- **Korean license content (summaries + obligations).** When the interface
  language is Korean, each classification-catalog license's plain-language
  summary and its obligation text now render in Korean, with the authoritative
  English original one click away (the canonical license text stays English).
  Covers the finite 52-license catalog; licenses outside it fall back to
  English. No schema change — the translations live in a code catalog and are
  attached to the API responses, with an EN↔KO drift contract test.
- **Policy-aware SBOM export profiles.** `GET /v1/projects/{id}/sbom` accepts
  an optional `profile`: `policy-annotated` flags each component that violates
  the project's effective license policy in place (CycloneDX `properties` /
  SPDX annotations, for forbidden and conditional licenses); `policy-filtered`
  drops forbidden components (and the vulnerability entries referencing them),
  recording the excluded count on the document. The default export is
  unchanged and byte-stable. Profile exports are **not** cosign-signed — the
  signature covers only the canonical default SBOM.

## [0.14.0] — 2026-07-16

### Added
- **Audit trail for external side effects.** Posting/updating a gate PR
  comment (`sca_pr_comment.posted` / `.updated`) and uploading a source
  archive (`source_archive.uploaded`) now write explicit audit rows with
  full request context — both actions previously left no trail because the
  automatic audit listener only sees DB rows. Explicit rows also run their
  diff through the sensitive-column masker.
- **Global mutation error toast (frontend).** A cache-level error handler
  guarantees no failed write stays silent: any mutation that does not
  surface its own error now raises an error toast with the RFC 7807
  `detail`. Existing call sites keep their local error UX via an explicit
  opt-out; 422 validation problems stay inline per the design system. The
  ErrorBoundary fallback is now translated (EN/KO) and announced via
  `role="alert"`.
- **EOL operations: weekly refresh beat + admin health panel.** A weekly
  Celery beat re-stamps the component catalog against the newest
  endoflife.date snapshot (so release upgrades reach existing rows without
  a re-scan, and stamps are cleared when the whitelist shrinks) and — only
  when `EOL_REFRESH_ENABLED=true`, off by default — fetches fresh lifecycle
  data with a sanity floor that stops a gutted sweep from displacing a good
  dataset. The admin/health page gains an endoflife.date snapshot panel
  (dataset age with a 180-day stale warning, flagged totals, last tick,
  next fire) at `GET /v1/admin/eol/health`.
- **End-of-life (EOL) component flagging.** Components matching a curated
  endoflife.date product whitelist (Spring Boot, Express, Django, Rails,
  Angular, Vue, Next.js, Symfony, Laravel, Spring Framework) are stamped
  with their lifecycle verdict on the shared catalog. The Components tab
  gains an EOL column/badge and an "EOL only" filter (`?eol=true`), the
  drawer an End-of-life row, and the project Overview an EOL count that
  deep-links to the filtered list. Verdicts come from a snapshot vendored
  with the release — zero network at scan time, air-gap safe
  (`EOL_SNAPSHOT_PATH` mounts a fresher snapshot; `EOL_ENABLED=false`
  disables).
- **iOS CocoaPods/SPM lockfile scanning.** A `Podfile` used to crash the
  whole source scan (cdxgen's cocoapods cataloger throws without the `pod`
  CLI). The scanner now excludes that cataloger and reconstructs pods —
  components AND dependency graph, subspecs included — offline from the
  committed `Podfile.lock`. Repos with only a committed `Package.resolved`
  now route to the swift environment, and the sidecar executors no longer
  re-run `swift package resolve` over a committed lockfile.
- **Runtime-scope SBOM filtering (default ON).** Source scans now drop
  non-deployable dependencies from the cdxgen SBOM before persist, signing and
  vulnerability matching: Maven `test`/`provided` nodes (cdxgen scope tags
  `optional`/`excluded`) and npm `devDependencies` (lockfile-classified `dev`).
  CVE counts and license obligations now describe the artifact that actually
  ships. **Component and CVE counts drop on the first re-scan of affected
  Maven/npm projects** — the scan summary records how many components were
  excluded, and `SCAN_SCOPE_FILTER_ENABLED=false` (or the per-ecosystem
  `SCAN_SCOPE_FILTER_MAVEN_ENABLED` / `SCAN_SCOPE_FILTER_NODE_ENABLED`)
  restores the full graph. SBOMs uploaded via the ingest API are never
  filtered — an uploaded SBOM is the supplier's declared truth.

### Fixed
- **Source-archive zip-ratio guard no longer rejects real OSS trees.** The
  flat 200x per-member compression-ratio ceiling blocked archives carrying
  tiny sparse fixtures (Juice Shop 17.0.0 ships two test PDFs at 918x/940x
  that inflate to ~150–225 KB). The ceiling now applies only to members
  declaring more than `SOURCE_ARCHIVE_RATIO_GUARD_MIN_BYTES` uncompressed
  (default 10 MiB); the streamed total-extracted cap remains the
  authoritative bomb guard and 42.zip-class members are still rejected. The
  rejection message now names the member and the resolving env knobs.
- **`seed_demo --demo-only` restores the documented 5-project quickstart.**
  The verify-baseline fixtures the default seed creates (per the
  seed-baseline agreement) had pushed the visible project list to 8, and
  the docs-uat quickstart-gate had failed nightly since 2026-06-10. The
  quickstart guide's seed command now uses the flag; the default seed is
  unchanged for the verify-specs nightly and Tier-3 runs.

### Security
- **Worker Go toolchain 1.25.11 → 1.25.12** — clears Go stdlib
  CVE-2026-39822 (`os.Root` symlink-following directory traversal, HIGH)
  on the bundled Go binaries and govulncheck. cosign/docker CLI carry the
  same stdlib finding with no upstream rebuild available yet — suppressed
  as UNREACHED with a re-evaluate deadline (`.trivyignore`).

## [0.13.1] — 2026-07-07

A fixes-only patch release: repairs fresh role-separated (L1) installs and
deployments, and picks up a security bump. No feature or schema changes.

### Fixed
- **L1 role provisioning: `trustedoss_app` was never created.**
  `scripts/postgres-init.sh` interpolated the role name / password inside a
  dollar-quoted `DO $$ … $$` block, where psql performs no variable
  substitution, so the literal `:'app_user'` reached the server and aborted the
  init script (`syntax error at or near ":"`). Every L1 backend then failed
  password auth as `trustedoss_app`. Role creation now uses `SELECT format(…)
  … \gexec` with `WHERE NOT EXISTS` — SQL-quoted and idempotent. (#466)
- **`AUTO_MIGRATE` was never plumbed into the container.** `install.sh` writes
  `AUTO_MIGRATE=false` on L1 stacks (migrations run once as the owner role), but
  the compose file never referenced `${AUTO_MIGRATE}`, so the backend entrypoint
  defaulted back to `true` and attempted DDL as the unprivileged app role. (#466)
- **install.sh L1 path: owner-password consistency + staged boot.** The secret
  block is now idempotent — `POSTGRES_PASSWORD` is the single source of truth for
  the owner password and is never rotated on re-run — and boot is staged
  (postgres+redis+backend → wait `/health` → owner-role `alembic upgrade head` →
  wait `/health/ready` → full fleet) so the worker's `depends_on backend:
  service_healthy` no longer deadlocks under `AUTO_MIGRATE=false`. A fresh
  install now also generates a strong random owner password instead of the
  shipped default. (#470)
- **Dev backend image now auto-migrates.** `apps/backend/Dockerfile` had a `CMD`
  but no `ENTRYPOINT`, so the dev container skipped `docker-entrypoint.sh` and
  never ran its migration — `/health/ready` stayed 503 and the backend was
  permanently unhealthy. It now carries the entrypoint like the production
  image. (#469)

### Security
- **`python-multipart` 0.0.30 → 0.0.31** — picks up the fix for CVE-2026-53540.
  Ships in the backend image. (#472)

### Internal
- Release notes are now stripped of Docusaurus front matter before being
  published to GitHub Releases (the closing `---` was rendering the metadata
  block as a giant heading). (#473)
- Release-gate CI hardening: cold-boot postgres readiness race, dev-runtime
  boot mode, docker-compose V1 nested-DSN interpolation, and health-gated
  (not `up`-exit-gated) patience. (#462, #463, #464, #465)
- Added L1 role-separated `install-uat` coverage and a `postgres-init` role
  contract gate. (#467, #468)

## [0.13.0] — 2026-07-04

A broad parity release closing the BomLens capability gap — additive throughout;
the one external-egress capability (SCANOSS) is off by default.

### Added
- **SCANOSS vendored-OSS identification (opt-in, off by default)** — an optional
  scan stage that fingerprints the source tree and matches copied-in
  ("vendored") open source against the SCANOSS knowledge base, recording
  full-file matches as components with detected licenses. This closes the gap
  for C/C++ / embedded trees that have no package manifest, where cdxgen alone
  finds almost nothing. It is **disabled by default** and gated on
  `SCANOSS_ENABLED=true`: unlike a local dev tool, a self-hosted portal must not
  send file fingerprints to an external API without explicit operator consent.
  When enabled, only file **fingerprints** (never source) are sent to
  `SCANOSS_API_URL` (default `api.osskb.org`, overridable for a self-hosted
  SCANOSS); snippet matches are skipped to keep results clean, and the stage
  degrades to a no-op on any error so a scan never fails because of it.
- **Global search (⌘K)** — the command palette (⌘K / Ctrl+K) gains cross-project
  **Components** and **CVEs** groups alongside Projects and Pages, backed by the
  new `GET /v1/search` endpoint. Results are scoped server-side to the caller's
  teams through a single `team_scope_filter` chokepoint — another team's
  components or vulnerabilities never appear. Component hits deep-link to the
  project's Components tab filtered to the term; CVE hits to its Vulnerabilities
  tab. Queries run from two characters, debounced, capped at 20 per group.
- **Dependency graph view** — the Components tab gains a **Table / Graph** toggle.
  The graph view renders the scan's resolved dependency graph (every parent →
  child edge the scanner recorded) as an interactive cytoscape node-link diagram
  with a severity-coloured node per component, a search highlight, and a
  click-to-detail panel — backed by the new
  `GET /v1/projects/{id}/dependency-graph` endpoint (serialised from the existing
  `component_dependency_edges` table; no migration). The choice mirrors into
  `?view=graph`. Graphs past the server node cap
  (`DEPENDENCY_GRAPH_MAX_NODES`, default 5000) or with no recorded edges fall
  back to a banner / collapsible tree so the view stays usable at scale.
- **Excel (`.xlsx`) vulnerability report** — the project vulnerability report can
  now be downloaded as an Excel workbook in addition to PDF, from the **Excel**
  button on the Reports tab's Vulnerability-report card (or
  `GET /v1/projects/{id}/vulnerability-report.xlsx`). The workbook has three
  sheets — Overview (risk score, severity + license distribution), Components,
  and Vulnerabilities (CVE, CVSS, EPSS, KEV state + due date, affected
  component) — and each download is recorded in the export history as
  `vuln_xlsx`. Cell values sourced from scanned third-party metadata are
  neutralised against spreadsheet formula injection (a value starting with
  `= + - @` is written as literal text — CWE-1236). This closes the CLAUDE.md
  "Excel / PDF reports" commitment, which previously shipped PDF only.
- **License classification catalog expansion (32 → 52 licenses)** — the license
  categoriser, obligation catalog, and bundled full-text set grew by 20 common
  SPDX licenses so fewer components land as `unknown`. New allowed (permissive)
  entries: BSL-1.0, Artistic-2.0, PostgreSQL, X11, NTP, Ruby, PHP-3.01, UPL-1.0,
  MIT-0, BlueOak-1.0.0, AFL-3.0, MS-PL, Libpng, CC-BY-4.0, curl, OpenSSL,
  BSD-4-Clause; new conditional (share-alike / reciprocal) entries: OFL-1.1,
  CC-BY-SA-4.0, MS-RL. Each ships its structured obligations and its verbatim
  SPDX full text for the NOTICE. A component that declares a license by
  **free-text name** with no SPDX id (e.g. `"Apache License, Version 2.0"`) is
  now run through an alias normaliser (ported from BomLens `spdx-normalize.jq`)
  and recovered as its canonical id when the name is a recognised alias;
  unfamiliar names stay `unknown` rather than being guessed. The three-way
  set (categoriser ↔ catalog ↔ bundled texts) is locked by a contract test.
- **AI license review flags** — the license catalog now carries two advisory
  "review needed" flags for AI-relevant restrictions that standard open-source
  compliance tooling misses: `behavioral_use` (RAIL / OpenRAIL and the Llama,
  Gemma, and Falcon community model licenses — behavioral-use restrictions) and
  `non_commercial` (CC-BY-NC and similar non-commercial terms). Flagged licenses
  show an amber "Review needed" badge and filter on the Compliance tab, and the
  generated NOTICE document gains a "License review needed" section. The flags
  report only the *existence* of a restriction class — whether it applies to a
  given use is a human / legal judgment (BomLens `license-flags.jq` /
  OpenChain AI SBOM principle). Ordinary licenses (MIT, Apache-2.0, GPL) are not
  flagged.
- **NOTICE license texts + per-component copyright** — the NOTICE document
  (text / markdown / html) now closes with a "License Texts" section embedding
  the full SPDX text of every license observed in the project (50+ license
  texts bundled — see the catalog-expansion entry above; a license without a
  bundled text falls back to its reference-URL link), and each component line carries the copyright statement
  recorded in the scan's SBOM — or, when the SBOM recorded none, an explicit
  fallback pointing at the component's registry URL (the line is never blank).
  The NOTICE artifact thereby satisfies the obligation catalog's
  `license_text_inclusion_required` obligation.
- **G7 AI SBOM minimum-elements conformance (advisory)** — SBOM ingest now
  accepts CycloneDX `specVersion` 1.7 (the ML-BOM model-card fields), and when
  an uploaded document carries a `machine-learning-model` component the
  conformance verdict appends the 51 G7 "SBOM for AI" minimum-element checks
  (7 clusters: metadata, system level properties, models, datasets properties,
  infrastructure, security properties, key performance indicators). Each
  element reports pass (present), advisory warn (absent), or "requires human
  review" (no automated source); G7 entries carry `cluster` / `source` /
  `role` / `evidence` fields in the `checks[]` array. All 51 are advisory —
  the overall pass / warn / fail verdict and its counters are unchanged.
  Registry and check semantics are vendored from BomLens
  (sktelecom/sbom-tools, Apache-2.0). No new env keys.
- **CISA KEV surfacing + Priority sort** — findings whose CVE is listed in the
  CISA KEV (Known Exploited Vulnerabilities) catalog carry a **KEV** badge and
  the catalog's remediation due date (`kev_due_date`) in the findings table and
  drawer. A new **Priority** sort — KEV first, then severity, then EPSS — is the
  default ordering. A daily Celery beat task (`trustedoss.kev_catalog_refresh`)
  syncs the CISA feed (~1,600 entries) into the vulnerability catalog,
  delistings included. New env keys: `KEV_FEED_URL`, `KEV_REFRESH_ENABLED`
  (set `false` on air-gapped deployments — KEV badges are then not shown), and
  `KEV_REFRESH_TIMEOUT_SECONDS`.
- **KEV operations closeout — admin feed panel + due-date status** —
  `/admin/health` gains a **KEV feed** panel: last successful sync time, live
  KEV-listed CVE count, listed / delisted counts from the last run, the next
  daily sync (01:45 UTC beat), and an OK / skipped (+reason) / disabled /
  never-run status backed by a new single-row `kev_sync_state` table the beat
  task upserts on every tick. A parsed feed below the 500-entry sanity floor
  is skipped like an outage (`skipped_reason: feed_below_sanity_floor`),
  preserving existing KEV flags so a gutted or truncated feed document can
  never mass-delist the catalog. The KEV badge in the findings table and
  drawer now grades the CISA remediation due date into three states — overdue
  (red) / due within 7 days (amber) / on track (neutral) — with a `D-n` /
  `D+n` day count.

### Fixed
- **Source scans no longer misclassify transitive dependencies as direct** when
  cdxgen emits the SBOM root's `dependencies` entry with an empty `dependsOn`
  (observed on Maven / Gradle source scans). The depth computation now trusts
  the metadata root only when it declares children and otherwise falls back to
  in-degree-0 root detection, so the Components TYPE column shows the real
  direct set. (#435)

## [0.12.0] — 2026-06-15

Two feature themes: **received-SBOM ingest with conformance scoring** (a customer
hands TRUSCA an SBOM their own tooling produced) and an **on-prem dynamic
per-environment scan executor** (the worker can launch a per-environment cdxgen
sidecar for a toolchain it does not carry, closing the Android gap). Both are
additive and opt-in — existing scans are unchanged.

Model 3 — **received-SBOM ingest with conformance scoring**. A customer can hand
TRUSCA an SBOM their own tooling already produced (rather than having TRUSCA
clone and build the source), and TRUSCA validates its quality, matches CVEs,
classifies licenses, and runs the build gate on it.

### Added
- **Received-SBOM ingest endpoint** — `POST /v1/projects/{project_id}/sbom-ingest`
  accepts an uploaded SBOM and queues an `sbom`-kind scan that persists the
  SBOM's components, matches CVEs with Trivy, and classifies declared licenses —
  no source clone or build. API-key or JWT auth, one in-flight scan per project,
  and the usual size / structure guards. (#404, #406)
- **SPDX input support** — both CycloneDX-JSON and SPDX (JSON and Tag-Value) are
  accepted. Trivy auto-detects the format for CVE matching; SPDX is mapped to
  CycloneDX internally for the component graph (no `spdx-tools` dependency).
  SPDX RDF/XML is not accepted. (#411)
- **SBOM conformance scoring** — every uploaded SBOM is scored for quality on its
  original bytes and gets a **pass / warn / fail** verdict. Mandatory checks:
  timestamp, tool info, a top-level component, 100% component name+version, PURL
  coverage ≥ `SBOM_CONFORMANCE_PURL_MIN_PCT` (default 90), no `pkg:generic`
  placeholders, and a transitive dependency graph; license and hash coverage are
  recommended (warn-only). The verdict is **advisory** — a `fail` is recorded and
  surfaced but does not block matching. Stored per scan, exposed at
  `GET /v1/projects/{project_id}/scans/{scan_id}/conformance`, and rendered as a
  badge + per-check table on the scan detail page. (#409, #410, #412)
- **`sbom` scan kind** in the UI — badge and admin queue filter label the new
  scan kind (EN / KO). (#408)

### Changed
- The `scan_kind` enum gained the `sbom` value, and the shared back-half of the
  source pipeline (component persistence → Trivy matching → finalize) was
  extracted to `tasks/_scan_pipeline` so the ingest task reuses it. (#404, #405)

### Documentation
- New CI-integration guide **Upload an SBOM** (endpoint, formats, conformance
  verdict; EN / KO), and the user-guide **Scans** / **SBOM** pages now document
  the `sbom` scan kind, received-SBOM upload, and the conformance verdict. (#413)

---

**Dynamic per-environment scan executor** (BomLens-style, on-prem). The
SBOM-generation stage is now pluggable: instead of always running cdxgen in the
worker, the worker can launch a per-environment **sidecar** container for a
toolchain it does not carry. Opt-in and on-prem single-tenant only; the default
is unchanged.

### Added
- **`SCAN_EXECUTOR=local_docker`** — an opt-in executor that launches a
  per-environment cdxgen sidecar over the host Docker socket, runs build-prep +
  cdxgen there, and collects the SBOM. The default `inprocess` executor is
  byte-for-byte unchanged. Behind a `ScanExecutor` abstraction with environment
  detection ported from BomLens `source-detect.sh`. (#417, #418, #419)
- **Android dependency-graph scanning** — the worker has no Android SDK, so the
  Android Gradle Plugin cannot resolve dependencies (0 components). Routing
  Android to the `sbom-scanner-android-sdk<API>` sidecar resolves the full graph
  (verified 0 → 67 components on a sample). Android is the default routed
  environment; the routed set is configurable via `SCAN_LOCAL_DOCKER_ENVS`. (#419, #422)
- **cdxgen output toggles** — `CDXGEN_SPEC_VERSION` (1.5 default, set 1.6) and
  `CDXGEN_FETCH_LICENSE` (off by default) tune the SBOM spec version and
  component-license resolution, applied by both the in-process and sidecar paths. (#420)
- **Sidecar security hardening** — `named` workspace-only volume mounting by
  default (never the cosign key), `--cap-drop=ALL` + the minimal build set,
  `no-new-privileges`, default memory / CPU / pids bounds, a curated env
  allow-list (no worker secrets), refusal of unpinned `:latest` images, an
  isolated egress network, an opt-in Docker socket proxy, and PEM-key redaction
  on sidecar output. Passed a Producer-Reviewer security review. (#421)

### Changed
- Generalized the sidecar executor from Android-only to any detected
  environment. Verification on Colima showed our all-in-one worker resolves
  transitive dependencies for node / go / rust / ruby / java / python / php /
  dotnet **identically** to the dedicated cdxgen language images, so those route
  only for per-build isolation (opt-in), not detection — Android is the one
  genuine gap and the only default-routed environment. (#422)

### Documentation
- New admin-guide page **Dynamic scan executor** (security model, in-code
  containment defaults, opt-in setup; EN / KO). A deferred implementation plan
  for the SaaS Kubernetes Job executor is recorded in
  the internal executor plan. (#421, #423)

## [0.11.1] — 2026-06-13

A UI / branding patch release. No backend or API changes — only the frontend
image, docs, and Helm chart metadata change versus `0.11.0`.

### Changed
- **Theme reverted to the W11 light theme.** The W13 "Google AI Studio"
  re-skin shipped in `0.11.0` (white canvas, blue primary, pill buttons) is
  rolled back to the W11 Vercel + Linear look (off-white canvas, warm
  near-black primary, square corners, blue Low badge). The TRUSCA brand and
  rename are unaffected.
- **New logo.** The mark is now a dark-slate tile (`#0f172a`) with a teal
  check accent (`#2dd4bf`) and an ink "TRUSCA" wordmark; the full lockup adds
  the tagline "TrustedOSS SCA" on the login gateway. Replaces the earlier
  flat-black and teal-gradient marks.
- **Complete favicon set.** Added `favicon.ico` (16 / 32 / 48) and an
  `apple-touch-icon.png` (iOS home screen) alongside the existing SVG, wired
  into `index.html` with a `theme-color`. Previously SVG-only.

### Fixed
- **Helm chart icon URL.** `Chart.yaml`'s `icon:` pointed at a non-existent
  path; it now resolves to
  `docs-site/static/img/logo.png` (a new 256×256 raster of the mark).

### Docs
- Regenerated the docs Open Graph social card with the new logo; added a
  README header logo; refreshed the design-system and brand reference pages.

## [0.11.0] — 2026-06-12

The first post-GA feature release. Headlines: the product is **renamed to
TRUSCA**, a public **read-only demo SaaS** deployable to a single Hetzner
server, a UI **craft pass** (W11–W12), and a hardening sweep from an external
verification campaign.

### Renamed — TrustedOSS Portal is now TRUSCA

**TRUSCA** (Trust + SCA) is the new product name — *the SCA tool of the
TrustedOSS initiative*. The umbrella initiative keeps the TrustedOSS name; the
tool gets its own. What changes for you:

- **Repository**: `github.com/trustedoss/trustedoss-portal` →
  `github.com/trustedoss/trusca`. Git remotes and old web links redirect
  automatically.
- **Docs site path**: `trustedoss.github.io/trustedoss-portal/` →
  `trustedoss.github.io/trusca/` (GitHub Pages does **not** redirect the old
  path — update bookmarks).
- **Container images** (BREAKING for upgrades): from 0.11.0 images publish as
  `ghcr.io/trustedoss/trusca-backend`, `trusca-backend-worker`, and
  `trusca-frontend`. Releases ≤ 0.10.0 keep their old image names, and an
  upgrade via `git checkout v0.11.0 && bash scripts/upgrade.sh` switches
  automatically (the new compose file pins the new names). Only custom
  overlays that hardcode the old image names need a manual edit.
- **Unchanged on purpose**: database user/roles, the Celery app name, the
  compose network, demo account e-mails, and `urn:trustedoss:*` problem URNs
  are internal identifiers that match the umbrella name and stay as-is.
- New brand: the "Hex Check" mark (package hexagon + verification check) and
  the first frontend favicon.

### Added
- **Public read-only demo mode** — `DEMO_READ_ONLY` makes the backend serve all
  reads but reject every write (allow-listing only the auth login/refresh/logout
  flow) with an RFC 7807 403. The SPA surfaces it as a banner, a login-page
  credentials hint, and a dedicated "read-only demo" toast on blocked writes.
- **Hetzner demo provisioning** — cloud-init, an operator runbook (EN/KO), an
  idempotent `seed_demo` dataset, a daily `reset_demo` wipe-and-reseed timer, and
  a daily backup timer.
- **Optional SSH-based CD** (`deploy-hetzner.yml`) — one-click / on-release deploy
  to the demo host via the existing `upgrade.sh`, with strict tag validation and
  host-key pinning.
- **Day-2 operations** — opt-in offsite backup (`backup-offsite.sh`, rclone), a
  backstop uptime canary workflow, and a Korean translation-style linter for the
  docs site.

### Changed
- **Visual & craft pass (W11–W12)** — modern-enterprise theme (warm near-black
  primary, off-white canvas), Inter/JetBrains-Mono typography system, an
  in-house global toast, CSS-only route/motion transitions with a reduced-motion
  guard, and richer empty/loading states.

### Fixed
- Drawer obligations, CVE deep-links, and the Compliance NOTICE toolbar
  (M-20/M-21/M-22). Relative-time displays now always carry an absolute-time title.

### Security
- Revoke the entire refresh-token family on reuse detection (C-1).
- Redact embedded `git_url` credentials on the read API and in audit logs (C-2).
- Enforce the project boundary for project-scoped API keys (M-2) and scope
  `GET /v1/audit` reads to the caller's team for team admins (M-3).
- Codified five testing-hardening rules and vendored the verification team's
  deterministic specs as a nightly regression gate.

## [0.10.0] — 2026-05-31

First public release of TrustedOSS Portal.

### Scope

A self-hosted, Apache-2.0 SCA portal covering vulnerability tracking,
license compliance, SBOM generation, and CI/CD integration in one UI.

### Highlights

#### Scanning
- **Source scans** — `cdxgen` generates a CycloneDX SBOM across 30+ language
  ecosystems; Trivy correlates components against its unified vulnerability DB
  (NVD + OSV + GitHub Advisory + EPSS + KEV).
- **Container scans** — Trivy on OS packages of an image reference.
- **Vulnerability re-detection** — weekly Trivy DB refresh + a Celery beat
  re-matches existing SBOMs against the refreshed feed, with notification
  channels firing on new criticals.
- **Air-gapped support** — `TRIVY_DB_REPOSITORY` can point at a private OCI
  mirror of the Trivy DB.
- **Scan retention** — results are keyed by project ref so each ref keeps its
  latest scan + findings (superseded scans retired automatically, a beat
  reclaims orphans, and manual `DELETE` is available) — no unbounded growth.

#### Compliance
- **License classification** — allowed / conditional / forbidden tiers,
  scored against a fixed catalog.
- **Obligations** — auto-generated `NOTICE` files (text / markdown / HTML).
- **Component approval workflow** — Pending → Under Review → Approved / Rejected.
- **VEX** — export and consumption (OpenVEX + CycloneDX VEX), 7-state triage.
- **SBOM export** — CycloneDX (JSON/XML) and SPDX (JSON/Tag-Value), byte-stable,
  with per-component license and version fields populated.
- **Forbidden-license waivers** — time-boxed waivers from the Compliance tab,
  capped by `LICENSE_WAIVE_MAX_DAYS` so a waiver cannot outlive its review.

#### CI/CD
- **GitHub Actions composite action** (`actions/scan/`) — trigger a scan and
  gate the build on Critical CVEs or forbidden licenses (`exit 1`).
- **GitHub & GitLab webhooks** — auto-trigger scans on push / PR events with
  inline PR/MR comments.
- **REST API + API Keys** — for Jenkins and other CI systems without a native
  integration; a Jenkinsfile example is shipped.
- **EPSS prioritization** — column, sort, filter, and a policy-gate threshold
  (`GATE_EPSS_THRESHOLD`).
- **API key expiry presets** — pick a TTL when minting a key from the
  Integrations form; keys carry an explicit expiry.
- **Self-scan hardened via dogfooding** — running our own scan-action against
  this repo surfaced and fixed an API-key scope rejection on trigger/poll
  (`401`) and a disjunctive-`OR` license misclassification.

#### Operations
- **Multi-tenant teams + RBAC** — `super_admin` / `team_admin` / `developer`.
- **Append-only audit log** — every write surfaced with diff + actor; SQL-level
  immutability via a `plpgsql` trigger.
- **Notifications** — email (SMTP), Slack, Microsoft Teams.
- **Admin UI** — user/team management, Trivy DB monitoring + weekly refresh,
  scan queue, disk dashboard, audit-log search/filter/CSV export.
- **Backups** — daily auto-backup via Celery beat + manual backup/restore from
  the Admin UI.
- **Self-hosted demo mode** — `DEMO_READ_ONLY=true` makes the deploy read-only.

#### Experience
- **EN + KO i18n** — every UI string and every documentation page is shipped
  in both languages from the first public release.
- **Modern enterprise design system** — light theme, WCAG AA contrast,
  compact 40 px tables, drawer + page navigation dual surfaces.
- **Filter URL persistence** — every filter facet (severity, license category,
  search, status, page) lives in the URL so reload / share / back-button
  restores the exact view.
- **Global ⌘K palette** — keyboard-first navigation across projects, vulns,
  components, and admin surfaces.
- **Portfolio Dashboard** — KPI cards + severity / license distribution +
  recent scans / activity, on `/`.
- **Collapsible sidebar + responsive shell** — the sidebar toggles to a 64 px
  icon rail (persisted) and collapses to a hamburger drawer below `lg`.

#### Distribution
- **Docker Compose** (dev + prod with Traefik + Let's Encrypt).
- **Helm chart** (`charts/trustedoss`) — bundled-or-external PostgreSQL &
  Redis, Ingress with cert-manager TLS, migration Job.
- **Hosted OpenAPI reference** at `/reference/api` on the docs site.
- **`/health/ready`** — schema-gated readiness probe; `503` until the Alembic
  schema is at HEAD.
- **Chart image tags pinned to the release** — `image.tag` defaults track
  `appVersion` (`0.10.0`) so a default `helm install` pulls matching images.

#### Quality
- **Documentation UAT harness** — the user/admin/CI guides are exercised
  end-to-end ("does it work as written?") with 38 auto-executed assertions
  across 23 enrolled docs, run nightly.
- **CI gates re-enabled** — SAST (Semgrep / Bandit), the Playwright e2e matrix,
  and supply-chain self-scan run on every change or nightly, with `main`
  branch protection enforcing the required checks.
