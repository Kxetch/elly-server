"""Manages the Telegram bot (telegram_bot/bot.py) as a child process of
elly-api, so a user never has to open a second terminal and run
`uv run elly-telegram` themselves, or uncomment a second Docker Compose
service.

Design: elly-api's own main() (see api/app.py) starts this whenever a
bot token is configured (Settings UI or the legacy ELLY_TELEGRAM_BOT_TOKEN
env var -- see domain/settings.py::get_effective_telegram_bot_token) and
stops it on shutdown/restart. bot.py itself is untouched -- it still
just reads ELLY_TELEGRAM_BOT_TOKEN from its own environment exactly as
before, so this manager passes the effective token through as that
child process's env var rather than teaching bot.py anything about the
database.

If the subprocess exits unexpectedly (crash, transient network error,
an invalid token causing Telegram's API to reject it once polling
starts), a background thread restarts it after a short, fixed delay --
deliberately NOT exponential backoff or a retry cap: this is a
single-user, personal app, not a multi-tenant service, so "just keep
trying every N seconds forever" is simple, predictable, and cheap
enough not to bother with more machinery. A deliberate stop() is never
mistaken for a crash (see _stop_requested).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("elly_server.telegram_bot.process_manager")

# Module-level so tests can monkeypatch them down to near-zero instead
# of actually waiting multiple real seconds per test.
MONITOR_POLL_INTERVAL_SECONDS = 1.0
RESTART_BACKOFF_SECONDS = 5.0


def _bot_command() -> list[str]:
    """Command to launch the bot subprocess with.

    Prefers the `elly-telegram` console script installed alongside
    this process's own interpreter (same venv -- true whether running
    via `uv run elly-api` natively or Docker's `CMD ["elly-api"]`,
    since both put elly-telegram in that same .venv/bin). Falls back to
    invoking the module directly for any environment where that script
    isn't discoverable there.
    """
    script = Path(sys.executable).parent / "elly-telegram"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "elly_server.telegram_bot.bot"]


class TelegramBotProcessManager:
    """Owns exactly one Telegram bot subprocess at a time.

    Not a singleton by class design (tests construct their own
    instances) -- see the module-level `telegram_process_manager`
    below for the one actually used by the running app.
    """

    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen] = None
        self._token: Optional[str] = None
        self._stop_requested = True  # nothing running yet
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, token: str) -> None:
        """Start the bot subprocess with `token`. If already running
        with this exact token, does nothing -- if running with a
        different (or no) token, stops the old one first."""
        with self._lock:
            if self._process is not None and self._process.poll() is None and self._token == token:
                return
            self._stop_locked()
            self._stop_requested = False
            self._token = token
            self._spawn_locked()

        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True, name="telegram-bot-monitor"
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        """Stop the bot subprocess, if running, and prevent the
        monitor thread from restarting it."""
        with self._lock:
            self._stop_requested = True
            self._stop_locked()

    def _spawn_locked(self) -> None:
        """Caller must hold self._lock."""
        env = {**os.environ, "ELLY_TELEGRAM_BOT_TOKEN": self._token or ""}
        cmd = _bot_command()
        logger.info("Starting Telegram bot subprocess: %s", " ".join(cmd))
        self._process = subprocess.Popen(cmd, env=env)  # noqa: S603 -- fixed, non-shell command

    def _stop_locked(self) -> None:
        """Caller must hold self._lock."""
        proc = self._process
        self._process = None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("Telegram bot subprocess didn't exit gracefully -- killing it")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("Telegram bot subprocess still alive after kill() -- giving up on it")

    def _monitor_loop(self) -> None:
        while True:
            time.sleep(MONITOR_POLL_INTERVAL_SECONDS)
            with self._lock:
                if self._stop_requested:
                    return
                proc = self._process
                token = self._token
            if proc is None:
                return
            if proc.poll() is None:
                continue  # still running, nothing to do

            logger.warning(
                "Telegram bot subprocess exited unexpectedly (code %s) -- "
                "restarting in %ss",
                proc.returncode,
                RESTART_BACKOFF_SECONDS,
            )
            time.sleep(RESTART_BACKOFF_SECONDS)
            with self._lock:
                # Re-check nothing changed (a deliberate stop()/start()
                # with a new token) while we were sleeping.
                if self._stop_requested or self._token != token or self._process is not proc:
                    continue
                self._spawn_locked()


# The one instance actually used by the running app (see api/app.py's
# main()). Tests construct their own TelegramBotProcessManager()
# instances instead of touching this one.
telegram_process_manager = TelegramBotProcessManager()
