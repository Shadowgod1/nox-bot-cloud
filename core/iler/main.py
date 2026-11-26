"""ILER core application: API, Evolution webhook, engine and background workers.

Run with a single uvicorn worker: the message buffer lives in process memory.
"""

import asyncio
import importlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from iler.config import VERSION, get_settings
from iler.db import migrate
from iler.db.pool import close_pool, init_pool
from iler.errors import install_error_handlers

log = logging.getLogger("iler")

API_MODULES = [
    "setup", "auth", "users", "dashboard", "whatsapp", "conversations", "bookings",
    "settings", "test_chat", "templates", "system", "backups", "events", "knowledge",
]
EXTRA_ROUTERS = ["iler.whatsapp.webhook", "iler.tools.senet.api", "iler.backup.internal_api"]
WORKERS = ["inbound_retry", "wa_monitor", "outbound_reconcile", "booking_timeout", "backup_scheduler"]
HOOKS = ["iler.engine.handler", "iler.bookings.admin_reply"]  # each exposes install()


async def _supervise(name: str, app: FastAPI) -> None:
    mod = importlib.import_module(f"iler.workers.{name}")
    while True:
        try:
            await mod.run(app)
            return  # worker finished on purpose
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("worker %s crashed; restarting in 5s", name)
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = await init_pool(settings.database_url)
    await migrate.run(pool)
    for hook in HOOKS:
        importlib.import_module(hook).install()
    tasks: list[asyncio.Task] = []
    if not settings.disable_workers:
        tasks = [asyncio.create_task(_supervise(w, app), name=f"worker:{w}") for w in WORKERS]
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await close_pool()


def create_app() -> FastAPI:
    # The schema is useful for development only; production does not advertise the API surface.
    openapi_url = "/api/openapi.json" if os.environ.get("ILER_OPENAPI") == "1" else None
    app = FastAPI(title="ILER Core", version=VERSION, lifespan=lifespan, openapi_url=openapi_url,
                  docs_url=None, redoc_url=None)
    install_error_handlers(app)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": VERSION}

    for name in API_MODULES:
        app.include_router(importlib.import_module(f"iler.api.{name}").router)
    for path in EXTRA_ROUTERS:
        app.include_router(importlib.import_module(path).router)
    return app


app = create_app()
