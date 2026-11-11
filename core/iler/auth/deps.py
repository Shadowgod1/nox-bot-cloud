"""Auth dependencies — placeholder, replaced by Task 2.

Every dependency rejects the request until the real implementation lands.
Tests override them through `app.dependency_overrides`.
"""

from iler.errors import ApiError


async def current_user():
    raise ApiError(401, "unauthorized", "Not authenticated")


async def require_owner():
    raise ApiError(401, "unauthorized", "Not authenticated")


async def csrf_protect():
    raise ApiError(401, "unauthorized", "Not authenticated")
