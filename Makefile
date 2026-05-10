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

COMPOSE      := docker-compose -f docker-compose.dev.yml
WORKER       := celery-worker

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
