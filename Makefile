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
	@echo "Guide screenshot capture (Playwright)"
	@echo "  make screenshots-capture   regenerate guide PNGs via tests/screenshots/"
	@echo "  make screenshots-clean     remove staging captures (keeps committed assets)"

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
.PHONY: screenshots-optimize
screenshots-optimize:
	@docker run --rm -v $(PWD)/$(SCREENSHOT_DIR):/work alpine:3.20 \
		sh -c 'apk add --no-cache oxipng pngquant >/dev/null && \
		       cd /work && \
		       for f in *.png; do \
		         [ -f "$$f" ] || continue; \
		         orig=$$(wc -c < "$$f"); \
		         pngquant --quality=75-90 --speed 1 --force --output - "$$f" 2>/dev/null \
		           | oxipng -o 4 --strip safe - --out - > "$$f.tmp" 2>/dev/null && \
		         mv "$$f.tmp" "$$f"; \
		         after=$$(wc -c < "$$f"); \
		         printf "%-55s %8d -> %8d (%d%%)\n" "$$f" "$$orig" "$$after" "$$((after * 100 / orig))"; \
		       done'
	@echo
	@echo "screenshots-optimize done. Review with `git diff --stat`. Re-run `make screenshots-capture` if visual regression is suspected."
