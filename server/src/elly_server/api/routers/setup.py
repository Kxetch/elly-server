"""First-run setup: the ONE deliberately unauthenticated surface.

`verify_token` lets a freshly-typed candidate token be checked before
it's stored in the browser -- this is safe to leave open because (a)
it never reveals the real token, only a boolean match, and (b) the
token itself is a 256-bit value generated locally and never sent
anywhere until a human reads it from the server's own startup output
and types it in by hand. Brute-forcing it here is computationally
infeasible even without rate limiting, but it's rate-limited anyway
for defense in depth (see api/rate_limit.py).
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from elly_server.api.rate_limit import limiter
from elly_server.api.schemas import VerifyTokenRequest
from elly_server.domain.auth import verify_token

router = APIRouter(prefix="/setup", tags=["setup"])


@router.post("/verify-token")
@limiter.limit("10/minute")
def verify_token_route(request: Request, payload: VerifyTokenRequest) -> dict[str, bool]:
    return {"valid": verify_token(payload.token)}
