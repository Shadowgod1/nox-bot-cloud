# nox-portal api: public API on :8000 (reached only by the portal's nginx) and the provisioner's internal
# API on :8001 (published on 127.0.0.1 only). Runs as an unprivileged user; no docker socket, no secrets
# beyond its own .env.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 NOX_MIGRATIONS_DIR=/app/migrations

RUN useradd --create-home --uid 10002 portal

WORKDIR /app
COPY pyproject.toml ./
COPY nox_portal ./nox_portal
RUN pip install . && rm -rf /root/.cache
COPY migrations ./migrations
COPY manage.py ./

USER portal
EXPOSE 8000 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1
CMD ["python", "-m", "nox_portal.main"]
