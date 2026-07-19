"""Runtime configuration for the Elly server.

Deliberately simple: this is a single-user, self-hosted app running on
one Mac, so there's no multi-tenant config, no secrets management
beyond a `.env` file, and all times are naive local wall-clock time
(see `elly_server.timeutil`).
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APP_NAME = "Elly"

# The app was previously named "KX" -- these are only used by the
# one-time migration in get_data_dir()/get_db_path() below, which
# moves an existing pre-rebrand install forward to the new name
# instead of silently starting fresh and orphaning real user data
# (diary entries, tasks, habits, the encryption key gating all of
# it). Safe to remove once you're confident no "KX"-named installs
# are still out there needing to migrate.
_OLD_APP_NAME = "KX"
_OLD_DB_FILENAME = "kx.db"


def _platform_base_dir() -> Path:
    """OS-appropriate base directory for per-app data folders.

    macOS: ~/Library/Application Support (Apple's own convention).
    Windows: %LOCALAPPDATA% (falls back to ~/AppData/Local if somehow
    unset). Linux/other POSIX: the XDG Base Directory spec
    ($XDG_DATA_HOME, or ~/.local/share if unset).
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    if sys.platform == "win32":
        local_appdata = os.environ.get("LOCALAPPDATA")
        return Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    return Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"


def _app_dir_name() -> str:
    """The per-app folder name -- lowercase on Linux (the conventional
    casing for ~/.local/share entries, and matching install-linux.sh's
    own node-toolchain path so the two don't end up sibling-but-
    differently-cased on a case-sensitive filesystem), the proper-cased
    APP_NAME on macOS/Windows (their own respective conventions)."""
    return APP_NAME if sys.platform in ("darwin", "win32") else APP_NAME.lower()


def get_data_dir() -> Path:
    """Directory where Elly stores its local data.

    Deliberately NOT inside iCloud Drive/OneDrive (or any other synced
    folder). A SQLite database being live-synced while open is a known
    corruption risk (the same issue other cloud-synced SQLite apps
    hit). Override with the ELLY_DATA_DIR env var if you need a
    different location (e.g. for tests).
    """
    override = os.environ.get("ELLY_DATA_DIR")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path

    base = _platform_base_dir()
    path = base / _app_dir_name()

    if sys.platform == "darwin":
        # One-time migration: pre-rebrand "KX" name -> "Elly", same base
        # dir. macOS only -- "KX" was a personal Mac app that predates
        # the Linux/Windows installers, so it never existed anywhere else.
        old_path = base / _OLD_APP_NAME
        if not path.exists() and old_path.exists():
            # A rename is atomic and same-volume here (both under the
            # same macOS base dir), so this never risks a partial/corrupt
            # copy.
            old_path.rename(path)
    else:
        # One-time migration: before this function was made OS-aware,
        # every platform (including Linux/Windows) used macOS's own
        # hardcoded path unconditionally -- move any data already
        # sitting there forward to the correct platform-appropriate
        # path. Uses shutil.move() rather than Path.rename(): unlike the
        # KX migration above, these two paths aren't guaranteed to be on
        # the same filesystem/mount point (e.g. ~/.local on a separate
        # partition), and a plain rename() raises across filesystems --
        # shutil.move() falls back to a copy+delete automatically in
        # that case.
        pre_fix_path = Path.home() / "Library" / "Application Support" / APP_NAME
        if not path.exists() and pre_fix_path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(pre_fix_path), str(path))

    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    override = os.environ.get("ELLY_DB_PATH")
    if override:
        return Path(override).expanduser()

    data_dir = get_data_dir()
    db_path = data_dir / "elly.db"
    old_db_path = data_dir / _OLD_DB_FILENAME

    if not db_path.exists() and old_db_path.exists():
        old_db_path.rename(db_path)
        # Carry over any live WAL/SHM journal files too, if present.
        for suffix in ("-wal", "-shm"):
            old_journal = data_dir / f"{_OLD_DB_FILENAME}{suffix}"
            if old_journal.exists():
                old_journal.rename(data_dir / f"elly.db{suffix}")

    return db_path


def get_database_url() -> str:
    return f"sqlite:///{get_db_path()}"


def get_api_port() -> int:
    """Port for the local REST API (`elly-api`)."""
    return int(os.environ.get("ELLY_API_PORT", "8765"))


def get_api_host() -> str:
    """Interface the REST API binds to inside its own process/container.

    Defaults to 127.0.0.1 -- for a native (non-Docker) install this IS
    the actual security boundary: nothing outside this machine can ever
    reach the port. Inside Docker this default would be unreachable
    through Docker's port publishing at all (Docker's NAT delivers
    traffic to the container's own network interface, not its
    loopback) -- so the Dockerfile overrides this to 0.0.0.0 via
    ELLY_API_HOST. In that case the REAL security boundary moves to
    docker-compose.yml's HOST-side port mapping (`127.0.0.1:8765:8765`,
    never `0.0.0.0:8765:8765`) -- see SECURITY.md for the full
    explanation. Never set this to 0.0.0.0 for a non-Docker install.
    """
    return os.environ.get("ELLY_API_HOST", "127.0.0.1")


def get_openai_api_key() -> str | None:
    """OpenAI API key for the in-app chat feature.
    Set the OPENAI_API_KEY environment variable or put it in a .env file.
    """
    return os.environ.get("OPENAI_API_KEY") or None


def get_openai_model() -> str:
    """Which model to use for in-app chat."""
    return os.environ.get("OPENAI_CHAT_MODEL", "gpt-4o-mini")


def get_ollama_base_url() -> str:
    """Base URL for a local Ollama server's OpenAI-compatible endpoint.

    Only used when the LLM provider (see domain/settings.py) is set to
    "ollama". Override if Ollama runs on a different host/port -- e.g.
    another machine on your LAN, or a different port inside Docker.
    """
    return os.environ.get("ELLY_OLLAMA_BASE_URL", "http://localhost:11434/v1")


def get_ollama_model() -> str:
    """Default local model name for the Ollama provider.

    Must be a model that supports tool/function calling (e.g. Llama
    3.1+, Qwen2.5, Mistral) -- older/smaller models may not reliably
    call Elly's tools. Can be overridden per-install in Settings.
    """
    return os.environ.get("ELLY_OLLAMA_MODEL", "llama3.1")


def get_telegram_bot_token() -> str | None:
    """Bot token for the optional Telegram remote-access channel (see
    domain/telegram.py). Get one from @BotFather on Telegram. Unset by
    default -- elly-telegram exits with a clear message if this is
    missing rather than the dashboard/MCP server ever requiring it."""
    return os.environ.get("ELLY_TELEGRAM_BOT_TOKEN") or None


def get_max_request_body_bytes() -> int:
    """Cap on incoming request body size, in bytes. Generous enough for
    long diary entries/chat messages, small enough to bound memory use
    from any single request. Defense-in-depth only -- see SECURITY.md."""
    return int(os.environ.get("ELLY_MAX_BODY_BYTES", str(2 * 1024 * 1024)))  # 2 MiB


def get_cors_origins() -> list[str]:
    """Origins allowed to call the REST API with credentials.

    Only matters during frontend development: the Vite dev server runs
    on its own port and needs CORS to reach this API's port. In
    production the built PWA will be served by this same process
    (same-origin, no CORS needed). Override with a comma-separated
    ELLY_CORS_ORIGINS if your dev server uses a different port.
    """
    override = os.environ.get("ELLY_CORS_ORIGINS")
    if override:
        return [origin.strip() for origin in override.split(",") if origin.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
