# TrustedOSS Portal — frontend (development image).
#
# Goals:
#   - npm dependencies install once at build time (`npm ci` against a
#     committed package-lock.json) instead of every container boot.
#   - Source still bind-mounts at runtime so Vite HMR + polling watcher work.
#   - node_modules lives inside the image and is masked by an anonymous volume
#     in docker-compose.dev.yml so the host's macOS filesystem can't shadow it.
#
# Production image (tsc -b && vite build → nginx) lands in PR #20 (Phase 7).

# CLAUDE.md core rule #9: pin tags, no `:latest`. Match docker-compose dev
# stack `node:20.18-alpine` plus a patch lock to avoid silent drift.
FROM node:20.18.1-alpine AS dev

WORKDIR /app

# Vite + esbuild ship prebuilt binaries; tini is enough for clean SIGTERM.
RUN apk add --no-cache tini

# Install dependencies first to maximize Docker layer cache hits.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# Source is bind-mounted at runtime; this COPY exists so the image is
# usable standalone (e.g. `docker run` without compose) and so the layer
# cache invalidation point is well-defined.
COPY . .

ENV HOST=0.0.0.0 PORT=5173
EXPOSE 5173

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
