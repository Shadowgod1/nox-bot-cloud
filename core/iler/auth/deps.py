"""PLACEHOLDER, replaced by Task 2 (auth). Do not extend.

Exists only so Task 3 routes can wire `Depends(require_owner)` / `Depends(csrf_protect)`.
Every dependency rejects the request, so nothing is reachable until the real module lands.
"""

from iler.errors import ApiError


async def current_user():
    raise ApiError(401, "unauthorized", "Not authenticated")


async def require_owner():
    raise ApiError(401, "unauthorized", "Not authenticated")


async def csrf_protect():
    raise ApiError(401, "unauthorized", "Not authenticated")
