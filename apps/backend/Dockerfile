FROM python:3.12.7-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# psycopg2 (sync, used by alembic) needs libpq + a C compiler;
# curl powers the docker-compose healthcheck;
# gnupg lets us add the PostgreSQL apt repo for postgresql-client-17.
#
# weasyprint (scan-gap G2 — vulnerability PDF report) needs native rendering
# libs at runtime: pango (text layout, pulls cairo + harfbuzz), gdk-pixbuf
# (raster images) and the matching shared objects. See the weasyprint install
# docs for Debian/bookworm. font packages give the PDF a usable default face.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        ca-certificates \
        gnupg \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        libgdk-pixbuf-2.0-0 \
        libffi-dev \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Marathon bundle 3 (D2) — install postgresql-client-17 so the backup
# integration tests (and any local CLI use of ``tasks.backup``) can drive
# ``pg_dump`` / ``psql`` end to end without standing up the worker
# container. Matches the worker image (apps/backend/Dockerfile.worker)
# so dev + CI exercise the same pg_dump binary the production worker
# would run.
#
# Supply chain (M4 — same posture as Dockerfile.worker): pin the PGDG
# key fingerprint so a compromised key URL cannot silently substitute a
# backdoored binary.
RUN curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /tmp/pgdg.asc \
    && gpg --show-keys --with-fingerprint --with-colons /tmp/pgdg.asc \
        | grep '^fpr:' \
        | grep -q ':B97B0AFCAA1A47F044F244A07FCC7D46ACCC4CF8:' \
    && gpg --dearmor -o /usr/share/keyrings/postgresql.gpg /tmp/pgdg.asc \
    && rm /tmp/pgdg.asc \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-dev.txt /app/
RUN pip install --upgrade pip \
    && pip install -r requirements-dev.txt

# Entrypoint applies Alembic migrations on container start (AUTO_MIGRATE=true,
# the dev default) before exec'ing CMD. Copy it EXPLICITLY with an executable
# mode BEFORE the broad `COPY . /app`, so the image layer carries the +x bit
# regardless of the host checkout's umask (BuildKit `--chmod`, same posture as
# Dockerfile.prod:75). The dev stack bind-mounts ./apps/backend over /app at
# runtime, so the file actually executed is the host copy (git-tracked mode
# 100755) — this explicit COPY additionally guarantees a self-consistent image
# for a plain `docker run` without the bind mount. Build context is
# ./apps/backend (docker-compose.dev.yml x-backend-build), so the source path is
# docker-entrypoint.sh, not apps/backend/docker-entrypoint.sh.
COPY --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh

COPY . /app

EXPOSE 8000

# nosemgrep: dockerfile.security.missing-user.missing-user — dev image only.
# uvicorn --reload watches the bind-mounted source tree, which requires
# matching uid/gid with the host. Production uses Dockerfile.prod which
# pins `USER trustedoss` (see Dockerfile.prod:61).
#
# v2.1 dev parity — route CMD through docker-entrypoint.sh so the dev backend
# runs `alembic upgrade head` on start (AUTO_MIGRATE, default true), exactly like
# the production image (Dockerfile.prod:98). Without this ENTRYPOINT the
# container exec'd uvicorn directly: alembic_version was never created,
# /health/ready stayed 503, and the backend never went healthy — so worker /
# beat (depends_on backend: service_healthy) never started and install.sh's
# `up -d` failed with "backend unhealthy". No tini here (not installed in the
# slim dev image; dev runs uvicorn --reload as root for bind-mount uid parity):
# the script `exec`s CMD so uvicorn replaces the shell and owns its own SIGTERM
# graceful shutdown — no signal-handling regression vs. running uvicorn as PID 1
# directly. CMD is unchanged; the entrypoint runs it via `exec "$@"`.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
