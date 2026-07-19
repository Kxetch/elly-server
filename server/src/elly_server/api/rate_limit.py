"""Shared slowapi Limiter instance.

Kept in its own module (not defined in app.py) so routers can import
and use `@limiter.limit(...)` without a circular import back to
app.py, which itself imports the routers.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
