<!--
Thanks for contributing to TrustedOSS Portal!
Please fill out every section. Empty checklists block review.
See CONTRIBUTING.md for the full PR process.
-->

## Summary

<!-- One or two sentences. What does this PR change, and why? -->

## Related Issues

<!-- Link the issue(s) this PR addresses. Use "Closes #N" to auto-close on merge. -->

- Closes #
- Refs #

## Type of Change

<!-- Check all that apply. -->

- [ ] `feat` — new user-visible feature
- [ ] `fix` — bug fix
- [ ] `refactor` — internal change without behavior change
- [ ] `docs` — documentation only
- [ ] `test` — tests only
- [ ] `chore` / `ci` / `build` — tooling, infra, dependencies
- [ ] `perf` — performance improvement

## Phase / Roadmap

<!-- Which Phase from docs/v2-execution-plan.md does this PR belong to? -->

- Phase: <!-- e.g. Phase 0 PR #4, Phase 1 PR #5 -->
- Roadmap reference: <!-- e.g. §3.1 task 0.8 -->

## Checklist

### Code quality

- [ ] Lint passes (`ruff check .` / `npm run lint`)
- [ ] Typecheck passes (`mypy .` / `tsc -b --noEmit`)
- [ ] No `:latest` Docker image tags introduced
- [ ] `docker-compose` (V1, hyphenated) used; `docker compose` (V2) NOT used
- [ ] `os.getenv()` called at runtime, not cached at module load

### Tests

- [ ] Unit tests added / updated for changed code
- [ ] Integration tests use real PostgreSQL / Redis (not mocks)
- [ ] Coverage stays at or above the floor (backend ≥ 80%, frontend ≥ 80% lines / 70% branches)
- [ ] Playwright E2E core scenarios still green
- [ ] **Harness updated or added** when introducing a new screen / domain

### Security

- [ ] No secrets, tokens, API keys, or PII in code or logs
- [ ] All endpoints require authentication unless explicitly documented as public
- [ ] RBAC dependency applied where applicable
- [ ] Errors return RFC 7807 `application/problem+json` shape
- [ ] If touching auth / API keys / DT integration / OAuth / build gate → `security-reviewer` agent invoked (Producer-Reviewer pattern)

### i18n

- [ ] All new user-visible strings extracted via `t()`
- [ ] English (`en/*.json`) translations added
- [ ] Korean (`ko/*.json`) translations added (parity with English)

### Documentation

- [ ] OpenAPI schema reflects new / changed endpoints
- [ ] Docusaurus page added / updated for the user-facing change
- [ ] `CLAUDE.md` / `docs/v2-execution-plan.md` / `MEMORY.md` consistent with this change

## Test Results

<!-- Paste relevant test output, or describe how you verified the change.
For UI changes, attach a screenshot or short clip. -->

```
# e.g.
$ pytest tests/unit tests/integration --cov
... 24 passed in 4.12s
Coverage: 84.7%
```

## Migration Notes

<!-- If the PR includes an Alembic migration or breaking change, describe the
upgrade path here. Remember: forward-only migrations. Schema and data
migrations are separate revisions. Breaking column changes follow
expand → migrate-data → contract. -->

## Reviewer Notes

<!-- Anything reviewers should focus on, alternatives you considered, or
follow-up work intentionally deferred to a later PR. -->

---

By submitting this pull request, I confirm that my contribution is licensed under the [Apache License 2.0](../LICENSE) and is my original work or properly attributed.
