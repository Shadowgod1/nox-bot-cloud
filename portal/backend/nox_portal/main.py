"""Two ASGI apps in one process: the public API (behind nginx and the tunnel) and the internal API for the
provisioner (its own port, published on 127.0.0.1 only). `python -m nox_portal.main` serves both."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nox_portal import alerts, db, gateway, payments, renewal
from nox_portal.api import client, internal, payments as payments_api, staff
from nox_portal.config import VERSION, get_settings
from nox_portal.errors import install_error_handlers

log = logging.getLogger("nox_portal")
# httpx logs every request URL at INFO: the Telegram alert URL carries the bot token
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _check_settings() -> None:
    s = get_settings()
    from nox_portal.security import subkey

    subkey("session")  # raises on a missing / short NOX_SECRET_KEY
    if s.email_mode not in ("smtp", "log", "memory"):
        raise RuntimeError("NOX_EMAIL_MODE must be smtp, log or memory")
    if s.email_mode == "log":
        log.warning("NOX_EMAIL_MODE=log: letters (codes, setup links) are printed to the log. Development only!")
    if s.smtp_security not in ("starttls", "ssl", "none"):
        raise RuntimeError("NOX_SMTP_SECURITY must be starttls, ssl or none")
    if not any((s.upstream_anthropic_key, s.upstream_openai_key, s.upstream_gemini_key)):
        log.warning("LLM gateway: no NOX_UPSTREAM_*_KEY is set; /llm/... answers 503 until one is")
    payments.check_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    _check_settings()
    pool = await db.init_pool(get_settings().database_url)
    applied = await db.migrate(pool)
    if applied:
        log.info("migrations applied: %s", ", ".join(applied))
    if gateway.client is None:
        gateway.client = gateway.make_client()
    alerts.client = gateway.client
    billing_task = None
    if get_settings().billing_tick_seconds > 0:
        billing_task = asyncio.create_task(renewal.loop())
    yield
    if billing_task is not None:
        billing_task.cancel()
        await asyncio.gather(billing_task, return_exceptions=True)
    await gateway.drain()
    await alerts.drain()
    if gateway.client is not None:
        await gateway.client.aclose()
        gateway.client = alerts.client = None
    await db.close_pool()


def _base(title: str, with_lifespan: bool) -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title=title, version=VERSION, lifespan=lifespan if with_lifespan else None,
        docs_url="/api/docs" if s.openapi else None, redoc_url=None,
        openapi_url="/api/openapi.json" if s.openapi else None,
    )
    install_error_handlers(app)

    @app.get("/health")
    async def health():
        await db.get_pool().fetchval("SELECT 1")
        return {"status": "ok", "version": VERSION}

    return app


def create_app() -> FastAPI:
    app = _base("Nox portal", with_lifespan=True)
    app.include_router(client.router)
    app.include_router(staff.router)
    app.include_router(payments_api.router)
    app.include_router(gateway.router)
    return app


def create_internal_app() -> FastAPI:
    """No lifespan of its own: it shares the pool opened by the public app in the same process."""
    app = _base("Nox portal internal", with_lifespan=False)
    app.include_router(internal.router)
    return app


async def _serve() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    public = create_app()
    internal_app = create_internal_app()
    host = os.environ.get("NOX_LISTEN_HOST", "0.0.0.0")
    # --proxy-headers is not used: the visitor IP comes from X-Real-IP set by our nginx (ratelimit.client_ip)
    public_server = uvicorn.Server(uvicorn.Config(public, host=host, port=8000, access_log=False,
                                                  proxy_headers=False, lifespan="on"))
    internal_server = uvicorn.Server(uvicorn.Config(internal_app, host=host, port=8001, access_log=False,
                                                    proxy_headers=False, lifespan="off"))
    public_task = asyncio.create_task(public_server.serve())
    while not public_server.started and not public_task.done():
        await asyncio.sleep(0.1)
    if public_task.done():
        await public_task
        return
    internal_task = asyncio.create_task(internal_server.serve())
    # one server stopping (signal or crash) stops the other, so docker restarts the whole container
    await asyncio.wait({public_task, internal_task}, return_when=asyncio.FIRST_COMPLETED)
    public_server.should_exit = True
    internal_server.should_exit = True
    await asyncio.gather(public_task, internal_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(_serve())
