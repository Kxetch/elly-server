"""The Elly REST API app.

Thin HTTP layer over `elly_server.domain.*`, for the PWA frontend in
`web/`. Run with `uv run elly-api` -- binds to 127.0.0.1 only by default
(see PLAN.md section 5, "the only network egress is the LLM call
itself"; see config.py's get_api_host() docstring for the Docker
exception to this and why it doesn't weaken the actual security
boundary). During frontend development, run this alongside `npm run
dev` in `web/` (which proxies `/api` here -- see `web/vite.config.ts`).
Once `web/` has been built (`npm run build`), this same process also
serves that build directly from `/` -- no separate frontend server
needed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from elly_server.api.deps import require_auth
from elly_server.api.rate_limit import limiter
from elly_server.api.routers import budget, calendar, chat, dashboard, dev_notes, export, habits, insights, memory, notes, notifications, ollama, reminders, settings, setup, system, tasks, telegram
from elly_server.config import get_api_host, get_api_port, get_cors_origins, get_max_request_body_bytes
from elly_server.db.base import get_session, init_db
from elly_server.domain import settings as settings_domain
from elly_server.domain.notifications import check_and_send
from elly_server.domain.reminders import check_and_send_reminders

logger = logging.getLogger("elly_server")

app = FastAPI(
    title="Elly API",
    description="Local REST API for Elly -- an ADHD-aware notebook/diary/calendar companion.",
    version="0.1.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# No cookies are used anywhere (auth is a manually-attached Bearer
# header, not a cookie), so allow_credentials stays False -- this
# closes off any cookie-based CSRF surface entirely rather than
# relying on the origin allow-list alone.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    """Reject oversized request bodies before they reach a route
    handler. Defense-in-depth only: this checks the client-reported
    Content-Length header, not actual bytes streamed, so it isn't a
    substitute for keeping the app loopback-only. See SECURITY.md."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > get_max_request_body_bytes():
                return JSONResponse(status_code=413, content={"detail": "Request body too large"})
        except ValueError:
            pass
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Basic hardening headers, applied even though this only ever
    binds to 127.0.0.1 -- defense in depth against a future embedded
    webview, browser extension, or LAN-exposure misconfiguration."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'"
    )
    return response


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Domain functions raise plain ValueError for both "not found" and
    "bad input" cases -- map those to sensible HTTP status codes here
    so no router needs its own try/except."""
    message = str(exc)
    status_code = 404 if "not found" in message.lower() else 400
    return JSONResponse(status_code=status_code, content={"detail": message})


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Unauthenticated: the one deliberately open surface (see api/routers/setup.py).
app.include_router(setup.router, prefix="/api")

# Every other router requires a valid local access token.
_protected_routers = (
    notes.router, calendar.router, tasks.router, habits.router, insights.router,
    memory.router, dashboard.router, notifications.router, chat.router,
    dev_notes.router, settings.router, telegram.router, export.router,
    ollama.router, system.router, budget.router, reminders.router,
)
for router in _protected_routers:
    app.include_router(router, prefix="/api", dependencies=[Depends(require_auth)])


def _web_dist_dir() -> Path:
    """Where the built PWA lives, if it's been built at all.

    Override with ELLY_WEB_DIST for testing; defaults to `web/dist`
    alongside this monorepo's `server/` directory. Both API routes
    (registered above) and this static mount can coexist on one
    process: Starlette matches the concrete `/api/*` routes first and
    only falls through to serving `web/dist` for everything else.
    """
    override = os.environ.get("ELLY_WEB_DIST")
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[4] / "web" / "dist"


_web_dist = _web_dist_dir()
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=_web_dist, html=True), name="web")
else:

    @app.get("/")
    def root() -> dict[str, str]:
        # `web/dist` doesn't exist yet -- build the frontend
        # (`cd web && npm run build`) to have this serve it instead.
        return {"name": "Elly API", "status": "ok", "docs": "/docs", "web_build": "not found"}


def _print_first_run_token() -> None:
    """Print the local access token ONCE, only when it's first
    generated -- never on subsequent boots, so it doesn't linger in
    logs (e.g. the macOS LaunchAgent's log file) indefinitely.

    Explicitly flushed: stdout is block-buffered (not line-buffered)
    when redirected to a file/log rather than a live terminal, so
    without a flush this can sit unseen in a buffer indefinitely if
    the process is later killed rather than exiting cleanly.
    """
    from elly_server.domain.auth import get_or_create_token

    token, was_created = get_or_create_token()
    if was_created:
        lines = [
            "",
            "=" * 70,
            "Elly first-run setup",
            "",
            "Your local access token (you'll need this once, to unlock the",
            "dashboard in your browser -- it's saved after that):",
            "",
            f"    {token}",
            "",
            f"Open http://127.0.0.1:{get_api_port()} and paste it in when asked.",
            "=" * 70,
            "",
        ]
        print("\n".join(lines), flush=True)


def _start_telegram_bot_if_configured() -> None:
    """Spawn the managed Telegram bot subprocess if a token is
    configured (Settings UI or the legacy ELLY_TELEGRAM_BOT_TOKEN env
    var) -- see telegram_bot/process_manager.py. A no-op if unset;
    Telegram remains entirely optional."""
    from elly_server.telegram_bot.process_manager import telegram_process_manager

    with get_session() as session:
        token = settings_domain.get_effective_telegram_bot_token(session)
    if token:
        telegram_process_manager.start(token)


def main() -> None:
    import uvicorn
    from apscheduler.schedulers.background import BackgroundScheduler

    init_db()
    _print_first_run_token()
    _start_telegram_bot_if_configured()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        lambda: _run_check(),
        "interval",
        seconds=60,
        id="notifications",
        replace_existing=True,
    )
    scheduler.add_job(
        lambda: _run_reminder_check(),
        "interval",
        seconds=60,
        id="reminders",
        replace_existing=True,
    )
    scheduler.start()

    try:
        uvicorn.run(app, host=get_api_host(), port=get_api_port())
    finally:
        scheduler.shutdown(wait=False)
        from elly_server.telegram_bot.process_manager import telegram_process_manager

        telegram_process_manager.stop()


def _run_check() -> None:
    """Run the notification check in a fresh DB session.

    Logs unexpected errors instead of silently swallowing them -- a
    prior silent `except Exception: pass` here masked a real NameError
    for an unknown length of time (see PLAN.md session 12).
    """
    try:
        with get_session() as session:
            check_and_send(session)
    except Exception:
        logger.exception("Scheduled notification check failed")


def _run_reminder_check() -> None:
    """Run the reminders/alarms check in a fresh DB session -- same
    isolation/logging rationale as _run_check() above (a separate
    scheduler job/session from the morning/evening check-ins, so one
    failing never blocks the other)."""
    try:
        with get_session() as session:
            check_and_send_reminders(session)
    except Exception:
        logger.exception("Scheduled reminder check failed")


if __name__ == "__main__":
    main()
