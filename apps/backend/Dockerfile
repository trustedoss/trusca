FROM python:3.12.7-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# psycopg2 (sync, used by alembic) needs libpq + a C compiler;
# curl powers the docker-compose healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
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
