FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 ILER_MIGRATIONS_DIR=/app/migrations

# pg_dump / pg_restore / psql / redis-cli are needed for backups. The PostgreSQL client major must
# match the server (postgres:16 in compose.yml): a newer pg_dump writes settings such as
# transaction_timeout that a 16 server rejects on restore. Debian ships only its own major, so the
# client comes from the PGDG repository.
ARG PG_MAJOR=16
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg postgresql-common redis-tools \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends "postgresql-client-${PG_MAJOR}" \
 && apt-get purge -y --auto-remove gnupg \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home --uid 10001 iler \
 && mkdir -p /data/backups && chown -R iler:iler /data

WORKDIR /app
COPY pyproject.toml ./
COPY iler ./iler
COPY migrations ./migrations
RUN pip install .

USER iler
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1
CMD ["uvicorn", "iler.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--no-access-log"]
