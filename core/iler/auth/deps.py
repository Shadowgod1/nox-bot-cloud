"""PLACEHOLDER, replaced by Task 2 (auth).

Created by Task 11 only so that routers can declare their dependencies. Every dependency
denies access; tests use `app.dependency_overrides`.
"""

from iler.errors import ApiError


async def current_user():
    raise ApiError(401, "unauthorized")


async def require_owner():
    raise ApiError(401, "unauthorized")


async def csrf_protect():
    raise ApiError(401, "unauthorized")
