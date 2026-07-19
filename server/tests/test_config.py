"""Tests for config.py -- environment variable defaults/overrides."""

from __future__ import annotations

import pytest

from elly_server import config


def test_api_host_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELLY_API_HOST", raising=False)
    assert config.get_api_host() == "127.0.0.1"


def test_api_host_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    # Used by the Docker image (see Dockerfile) -- Docker's port
    # publishing can't reach a process bound only to 127.0.0.1 inside
    # the container, so the image overrides this. The actual host-level
    # exposure boundary lives in docker-compose.yml's port mapping, not
    # here -- this test only confirms the override mechanism works.
    monkeypatch.setenv("ELLY_API_HOST", "0.0.0.0")
    assert config.get_api_host() == "0.0.0.0"


def test_api_port_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELLY_API_PORT", raising=False)
    assert config.get_api_port() == 8765
    monkeypatch.setenv("ELLY_API_PORT", "9000")
    assert config.get_api_port() == 9000


def test_ollama_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELLY_OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("ELLY_OLLAMA_MODEL", raising=False)
    assert config.get_ollama_base_url() == "http://localhost:11434/v1"
    assert config.get_ollama_model() == "llama3.1"


def test_telegram_bot_token_unset_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELLY_TELEGRAM_BOT_TOKEN", raising=False)
    assert config.get_telegram_bot_token() is None


def test_max_request_body_bytes_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELLY_MAX_BODY_BYTES", raising=False)
    assert config.get_max_request_body_bytes() == 2 * 1024 * 1024


class TestPlatformDataDir:
    """get_data_dir() uses each OS's own convention -- macOS's ~/Library/
    Application Support (unchanged, see test_rebrand_migration.py for its
    dedicated coverage), %LOCALAPPDATA% on Windows, and the XDG Base
    Directory spec on Linux. Found and fixed after live-testing
    install-linux.sh/install-windows.ps1 revealed the path was previously
    hardcoded to the macOS convention on every platform (see PLAN.md)."""

    def test_linux_defaults_to_xdg_data_home_fallback(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(config.sys, "platform", "linux")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        result = config.get_data_dir()

        # Lowercase "elly" -- matches install-linux.sh's own
        # node-toolchain path convention, not the proper-cased APP_NAME.
        assert result == tmp_path / ".local" / "share" / "elly"
        assert result.exists()

    def test_linux_respects_xdg_data_home_override(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        xdg_dir = tmp_path / "custom-xdg-data"
        monkeypatch.setenv("XDG_DATA_HOME", str(xdg_dir))
        monkeypatch.setattr(config.sys, "platform", "linux")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        result = config.get_data_dir()

        assert result == xdg_dir / "elly"

    def test_windows_defaults_to_localappdata(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        local_appdata = tmp_path / "AppData" / "Local"
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        result = config.get_data_dir()

        # Proper-cased "Elly" on Windows -- matches install-windows.ps1's
        # own convention, unlike the lowercase Linux case above.
        assert result == local_appdata / "Elly"

    def test_windows_falls_back_when_localappdata_unset(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        result = config.get_data_dir()

        assert result == tmp_path / "AppData" / "Local" / "Elly"

    def test_linux_migrates_data_from_the_old_hardcoded_macos_style_path(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulates someone who ran install-linux.sh before this fix --
        # their data would have landed at the macOS-style path since
        # get_data_dir() used to hardcode it on every platform.
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(config.sys, "platform", "linux")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "Elly"
        old_dir.mkdir(parents=True)
        (old_dir / "elly.db").write_text("pretend-sqlite-bytes")

        result = config.get_data_dir()

        new_dir = tmp_path / ".local" / "share" / "elly"
        assert result == new_dir
        assert (new_dir / "elly.db").read_text() == "pretend-sqlite-bytes"
        assert not old_dir.exists()  # moved, not copied -- no orphaned duplicate

    def test_windows_migrates_data_from_the_old_hardcoded_macos_style_path(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        local_appdata = tmp_path / "AppData" / "Local"
        monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
        monkeypatch.setattr(config.sys, "platform", "win32")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "Elly"
        old_dir.mkdir(parents=True)
        (old_dir / "elly.db").write_text("pretend-sqlite-bytes")

        result = config.get_data_dir()

        assert result == local_appdata / "Elly"
        assert (result / "elly.db").read_text() == "pretend-sqlite-bytes"
        assert not old_dir.exists()

    def test_does_not_migrate_if_the_new_path_already_has_data(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(config.sys, "platform", "linux")
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "Elly"
        old_dir.mkdir(parents=True)
        (old_dir / "elly.db").write_text("old-and-stale")

        new_dir = tmp_path / ".local" / "share" / "elly"
        new_dir.mkdir(parents=True)
        (new_dir / "elly.db").write_text("current")

        config.get_data_dir()

        # Never overwrite/merge automatically -- same principle as the
        # existing KX-rebrand migration's own equivalent test.
        assert (old_dir / "elly.db").read_text() == "old-and-stale"
        assert (new_dir / "elly.db").read_text() == "current"
