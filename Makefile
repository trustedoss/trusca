# TrustedOSS Portal — operator make targets.
#
# Thin wrappers around docker-compose for routine dev-stack operations.
# Targets are grouped:
#   dev-up / dev-down                — bring the stack up / down
#   dev-rebuild-worker               — recover from a stale worker image
#   dev-reset                        — destroy + recreate (delegates to script)
#   dev-logs / dev-ps                — tail logs / list services
#
# Required: docker-compose V1 (hyphen). CLAUDE.md core rule #10.

COMPOSE        := docker-compose -f docker-compose.dev.yml
WORKER         := celery-worker
FRONTEND_DIR   := apps/frontend
SCREENSHOT_DIR := docs-site/static/img/screenshots
SCREENSHOT_STAGING := $(SCREENSHOT_DIR)/staging
WALKTHROUGH_DIR := docs-site/static/img/walkthroughs
WALKTHROUGH_RAW  := apps/frontend/tests/walkthroughs/.output

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "TrustedOSS Portal — dev-stack targets"
	@echo "  make dev-up                bring up the dev stack (detached)"
	@echo "  make dev-down              stop the dev stack (preserves volumes)"
	@echo "  make dev-rebuild-worker    rebuild celery-worker --no-cache + force-recreate"
	@echo "  make dev-reset             scripts/dev-reset.sh (destroys volumes!)"
	@echo "  make dev-reset-rebuild     dev-reset + worker rebuild + e2e seed"
	@echo "  make dev-logs              tail backend + worker logs"
	@echo "  make dev-ps                list service health"
	@echo ""
	@echo "Local CI checks (same commands as the lint / typecheck jobs)"
	@echo "  make check                 everything those two jobs run"
	@echo "  make check-backend         ruff + mypy + ai-review selftest ONLY"
	@echo "  make check-frontend        eslint, tsc, i18n, tokens and the repo linters ONLY"
	@echo "                             (the two partial targets are not what CI runs)"
	@echo ""
	@echo "Guide screenshot capture (Playwright)"
	@echo "  make screenshots-capture   regenerate guide PNGs via tests/screenshots/"
	@echo "  make screenshots-clean     remove staging captures (keeps committed assets)"
	@echo ""
	@echo "Animated walkthroughs (Playwright + ffmpeg)"
	@echo "  make walkthroughs-capture  record webm via tests/walkthroughs/"
	@echo "  make walkthroughs-encode   convert webm to mp4 + gif under $(WALKTHROUGH_DIR)/"

# ER62 — one entry point for the checks CI's lint and typecheck jobs run, so
# nobody has to decide per change which of them are worth running. Deciding by
# the shape of a change is how a test-only edit skips mypy.
#
# The command list lives in tools/local-ci/run.py and is compared against
# .github/workflows/ci.yml by tests/unit/test_local_ci_matches_ci.py, so it
# cannot fall behind CI without a test failing.
#
# A missing tool FAILS rather than being skipped: a run that quietly covered
# less than it appears to and still ended green would be worse than no target.
.PHONY: check
check:
	@python3 tools/local-ci/run.py --scope all

# Deliberately named for their scope. Passing one of these is NOT the same as
# passing what CI runs, and the runner says so on the last line.
.PHONY: check-backend
check-backend:
	@python3 tools/local-ci/run.py --scope backend

.PHONY: check-frontend
check-frontend:
	@python3 tools/local-ci/run.py --scope frontend

.PHONY: dev-up
dev-up:
	$(COMPOSE) up -d

.PHONY: dev-down
dev-down:
	$(COMPOSE) down

.PHONY: dev-rebuild-worker
dev-rebuild-worker:
	$(COMPOSE) build --no-cache $(WORKER)
	$(COMPOSE) up -d --force-recreate $(WORKER)

.PHONY: dev-reset
dev-reset:
	bash scripts/dev-reset.sh

.PHONY: dev-reset-rebuild
dev-reset-rebuild:
	bash scripts/dev-reset.sh --rebuild-worker --seed --no-prompt

.PHONY: dev-logs
dev-logs:
	$(COMPOSE) logs -f backend $(WORKER)

.PHONY: dev-ps
dev-ps:
	$(COMPOSE) ps

# ────────────────────────────────────────────────────────────────────
# Guide screenshot capture
#
# Drives `tests/screenshots/capture.spec.ts` via the dedicated Playwright
# config (`playwright.screenshots.config.ts`) so the e2e CI matrix never
# triggers a capture run accidentally. Output PNGs land directly under
# `$(SCREENSHOT_DIR)/` so the EN + KO Markdown share a single asset via
# the absolute `/img/screenshots/<file>.png` reference.
#
# Pre-requisites:
#   - docker-compose dev stack healthy (the SPA must render against the
#     real backend; `make dev-up` is enough for fresh stacks).
#   - python3 on PATH for the seed helper (apps/frontend/tests/_harness/seed.ts).
#
# NOT the way to refresh what ships (R1-7). Capture the committed assets on
# CI:
#
#   gh workflow run ui-gates.yml --ref <branch> -f capture_screenshots=true
#   gh run download <id> -n doc-screenshots
#
# The runner seeds from an empty database. A developer's stack carries every
# project any previous run created, and capturing there put "APPROVALS
# WAITING 213" and ten seeded project names into the user guide. This target
# stays for checking a single screen while working on it — look at what it
# produces, do not commit it.
# ────────────────────────────────────────────────────────────────────

.PHONY: screenshots-capture
screenshots-capture:
	cd $(FRONTEND_DIR) && npx playwright test --config=playwright.screenshots.config.ts

.PHONY: screenshots-clean
screenshots-clean:
	rm -rf $(SCREENSHOT_STAGING)
	@echo "removed $(SCREENSHOT_STAGING) (committed assets under $(SCREENSHOT_DIR) untouched)"

# Marathon bundle 9 (4f) — PNG compression automation.
# Runs oxipng (lossless) followed by pngquant (perceptual lossy quant).
# pngquant before oxipng would inflate the file; oxipng before pngquant
# loses oxipng's DEFLATE pass on the post-quant bitstream — pngquant
# pipes to oxipng in one shot for the optimal size.
#
# Tools are installed in a tiny Alpine container so operators do not
# have to apt/brew install on the host. The container mounts the
# screenshot dir read-write; processed files replace originals
# in-place. Idempotent — re-running after a clean capture saves a few
# more bytes from any pixel-noise drift.
#
# Quality:
#   - oxipng -o 4         — exhaustive level 4 (vs the brutal -o max
#                           which costs minutes for ~5% extra savings).
#   - pngquant 75-90      — quality floor 75, ceiling 90; the -- forces
#                           output to stdout so we can pipe to oxipng.
#                           No --skip-if-larger; we accept marginal
#                           "no-shrinkage" PNGs to keep the runner
#                           simple (the size-gate workflow catches
#                           regressions overall, not per-file).
# The `-s` guard on the temp file is not defensive programming — the `&&`
# chain it replaced destroyed all 44 assets. `> "$f.tmp"` succeeds whether
# or not the pipeline behind it wrote anything, so a failing oxipng left an
# empty file that `mv` then moved over the original. Every screenshot became
# 0 bytes, and the recipe reported "-> 0 (0%)" for each one as though that
# were a compression ratio.
#
# G0-5 — `--stdout`, not `--out -`. oxipng has no convention that `-` means
# standard output: it took the argument as a literal filename, wrote the
# optimized bytes into a file called `-`, and left its actual stdout empty.
# The `-s` guard above then did its job and kept every original, so the target
# was a no-op that reported "-> same size" on all 44 files while quietly
# leaving a `screenshots/-` artifact behind. The flag it wanted all along is
# `--stdout` (oxipng 9.x). With it the pipeline compresses as designed:
# 128,443 -> 38,988 bytes on the first asset measured, and no stray file.
.PHONY: screenshots-optimize
screenshots-optimize:
	@docker run --rm -v $(PWD)/$(SCREENSHOT_DIR):/work alpine:3.20 \
		sh -c 'apk add --no-cache oxipng pngquant >/dev/null && \
		       cd /work && \
		       for f in *.png; do \
		         [ -f "$$f" ] || continue; \
		         orig=$$(wc -c < "$$f"); \
		         pngquant --quality=75-90 --speed 1 --force --output - "$$f" 2>/dev/null \
		           | oxipng -o 4 --strip safe - --stdout > "$$f.tmp" 2>/dev/null; \
		         if [ -s "$$f.tmp" ]; then mv "$$f.tmp" "$$f"; else rm -f "$$f.tmp"; fi; \
		         after=$$(wc -c < "$$f"); \
		         printf "%-55s %8d -> %8d (%d%%)\n" "$$f" "$$orig" "$$after" "$$((after * 100 / orig))"; \
		       done'
	@echo
	@# Single quotes, not backticks. Backticks in a recipe are command
	@# substitution: this line used to RUN `git diff --stat` and, worse,
	@# `make screenshots-capture` — so optimising the assets silently
	@# recaptured them from the local database, overwriting a set that had
	@# just been pulled from CI. The message said "re-run if you suspect a
	@# regression" while re-running unconditionally.
	@echo "screenshots-optimize done. Review with 'git diff --stat'. Re-run 'make screenshots-capture' if visual regression is suspected."

# Marathon bundle 9 (4c) — Animated walkthroughs.
#
# Two-step pipeline:
#   1. ``walkthroughs-capture`` — runs the dedicated Playwright config
#      that records each spec as a webm (1440x900, video=on).
#   2. ``walkthroughs-encode``  — postprocesses the webm files into
#      mp4 (h264 baseline, ~700kbps, suitable for the docs <video>
#      tag) + a low-FPS gif preview (24fps -> 12fps decimate, palette
#      generation for sub-2MB output).
#
# Why two targets and not one: capture runs against the dev stack and
# may need re-runs while iterating on the user flow. Encode is purely
# CPU-bound and benefits from caching across iterations once the
# webm is captured cleanly. Splitting also lets CI run encode-only
# on artifact uploaded by an operator (no headless browser needed).
#
# The encode step pairs each spec's webm with the slug declared in
# the spec via ``test.info().annotations.push({type: "slug", ...})``.
# The slug lives in ``test-results.json`` next to the video. We avoid
# the brittle dance with Playwright's auto-generated test-output
# directory names — slug-driven naming is stable across spec renames.
.PHONY: walkthroughs-capture
walkthroughs-capture:
	cd $(FRONTEND_DIR) && npx playwright test --config=playwright.walkthroughs.config.ts

.PHONY: walkthroughs-encode
walkthroughs-encode:
	@bash scripts/encode-walkthroughs.sh "$(WALKTHROUGH_RAW)" "$(WALKTHROUGH_DIR)"

.PHONY: walkthroughs-clean
walkthroughs-clean:
	rm -rf $(WALKTHROUGH_RAW)
	@echo "removed $(WALKTHROUGH_RAW) (committed assets under $(WALKTHROUGH_DIR) untouched)"
