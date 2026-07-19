"""System-level operations: self-restart to apply changes that need a
fresh process.

Currently the only such change is the Telegram bot token (see
domain/settings.py, telegram_bot/process_manager.py) -- LLM/Ollama
settings already take effect live, no restart needed. Deliberately its
own tiny router rather than folded into settings.py: restarting the
whole process is a fundamentally different kind of operation (and
blast radius) than a normal settings PUT.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from fastapi import APIRouter

logger = logging.getLogger("elly_server")

router = APIRouter(prefix="/system", tags=["system"])

# Long enough for the HTTP response below to actually flush back to the
# client's socket before the process replaces itself; short enough
# that "restarting" doesn't feel like it hung.
RESTART_DELAY_SECONDS = 0.75


def _do_restart() -> None:
    """Replace this process's image in place via os.execv -- same PID,
    fresh Python interpreter, re-reads all env vars/config/DB settings
    from scratch. Works identically under Docker (the container keeps
    running; just the one process inside it restarts) and native
    `uv run elly-api` -- no LaunchAgent/systemd/supervisor required.

    Always re-invokes via `-m elly_server.api.app` rather than trying
    to reconstruct however the current process was originally started
    (a bare console-script path, `uv run elly-api`, Docker's exec-form
    CMD, ...) -- sys.argv[0] isn't reliably a re-runnable path across
    all of those, but the module path always is.

    Pulled out as its own top-level function specifically so tests can
    monkeypatch it directly rather than ever actually calling
    os.execv (which would replace the *test process* itself).
    """
    logger.info("Restarting elly-api (os.execv)")
    os.execv(sys.executable, [sys.executable, "-m", "elly_server.api.app"])


def _perform_restart() -> None:
    time.sleep(RESTART_DELAY_SECONDS)
    try:
        from elly_server.telegram_bot.process_manager import telegram_process_manager

        telegram_process_manager.stop()
    except Exception:
        # Best-effort: even if cleanly stopping the Telegram bot
        # subprocess fails, still restart rather than get stuck --
        # see process_manager.py's own error handling for the more
        # common failure modes this already covers.
        logger.exception("Failed to stop Telegram bot subprocess before restart")
    _do_restart()


@router.post("/restart")
def restart() -> dict[str, str]:
    """Schedule a restart shortly after responding, so the HTTP
    response actually reaches the client before the process replaces
    itself."""
    threading.Thread(target=_perform_restart, daemon=True, name="elly-restart").start()
    return {"status": "restarting"}
