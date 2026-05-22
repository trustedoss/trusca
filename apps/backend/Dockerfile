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

COPY . /app

EXPOSE 8000

# nosemgrep: dockerfile.security.missing-user.missing-user — dev image only.
# uvicorn --reload watches the bind-mounted source tree, which requires
# matching uid/gid with the host. Production uses Dockerfile.prod which
# pins `USER trustedoss` (see Dockerfile.prod:61).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
