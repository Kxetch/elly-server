"""Tests for the "KX" -> "Elly" rebrand migration paths.

These are the highest-stakes new code in the rebrand: get_data_dir()/
get_db_path() move an existing install's directory and DB file forward
to the new name, and domain/auth.py's get_or_create_token() /
domain/crypto.py's get_or_create_key() carry an existing token/
encryption key forward rather than silently generating new ones (which
for the encryption key specifically would make all previously-encrypted
content permanently unreadable). Every test here simulates a
pre-rebrand ("KX"-named) install and confirms the post-rebrand code
finds and migrates it correctly, without ever generating something new
when something old already exists.
"""

from __future__ import annotations

import sys

import pytest

from elly_server import config
from elly_server.domain import auth, crypto


@pytest.mark.skipif(
    sys.platform != "darwin",
    reason=(
        "config.get_data_dir()'s KX->Elly directory migration is deliberately "
        "macOS-only (see its own docstring/comment: 'KX' was a personal Mac "
        "app that predates the Linux/Windows installers, so it never existed "
        "anywhere else) -- on any other platform get_data_dir() uses a "
        "completely different base directory (e.g. ~/.local/share on Linux) "
        "and never runs this migration at all, so these path assertions "
        "(hardcoded to macOS's Library/Application Support layout) don't "
        "apply there. Found failing on a real Linux CI runner before this "
        "guard existed -- always run the real suite in CI, on the actual "
        "target platform, not just locally on a Mac, before trusting green."
    ),
)
class TestDataDirMigration:
    def test_migrates_an_existing_kx_directory_to_elly(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        old_dir = tmp_path / "Library" / "Application Support" / "KX"
        old_dir.mkdir(parents=True)
        (old_dir / "kx.db").write_text("pretend-sqlite-bytes")
        (old_dir / "some-other-file.txt").write_text("should survive the move too")

        result = config.get_data_dir()

        new_dir = tmp_path / "Library" / "Application Support" / "Elly"
        assert result == new_dir
        assert new_dir.exists()
        assert not old_dir.exists()  # moved, not copied -- no orphaned duplicate
        assert (new_dir / "kx.db").read_text() == "pretend-sqlite-bytes"
        assert (new_dir / "some-other-file.txt").exists()

    def test_creates_a_fresh_elly_directory_when_no_kx_install_exists(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        result = config.get_data_dir()

        assert result == tmp_path / "Library" / "Application Support" / "Elly"
        assert result.exists()

    def test_does_not_touch_an_existing_kx_dir_if_elly_dir_already_exists(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELLY_DATA_DIR", raising=False)
        monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

        base = tmp_path / "Library" / "Application Support"
        old_dir = base / "KX"
        new_dir = base / "Elly"
        old_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        (old_dir / "kx.db").write_text("old")
        (new_dir / "elly.db").write_text("new")

        config.get_data_dir()

        # Already migrated (or a fresh Elly install coexisting with a
        # leftover KX dir) -- never overwrite/merge automatically.
        assert (old_dir / "kx.db").read_text() == "old"
        assert (new_dir / "elly.db").read_text() == "new"


class TestDbPathMigration:
    def test_renames_kx_db_and_journal_files(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ELLY_DATA_DIR", str(tmp_path))
        (tmp_path / "kx.db").write_text("pretend-db-bytes")
        (tmp_path / "kx.db-wal").write_text("wal-bytes")
        (tmp_path / "kx.db-shm").write_text("shm-bytes")

        result = config.get_db_path()

        assert result == tmp_path / "elly.db"
        assert result.read_text() == "pretend-db-bytes"
        assert (tmp_path / "elly.db-wal").read_text() == "wal-bytes"
        assert (tmp_path / "elly.db-shm").read_text() == "shm-bytes"
        assert not (tmp_path / "kx.db").exists()

    def test_no_migration_needed_for_a_fresh_install(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ELLY_DATA_DIR", str(tmp_path))
        result = config.get_db_path()
        assert result == tmp_path / "elly.db"
        assert not result.exists()  # nothing to migrate, nothing created yet either


class TestTokenMigration:
    def test_carries_an_existing_kx_token_file_forward(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
        (tmp_path / ".kx_token").write_text("pre-existing-real-token-value\n")

        token, was_created = auth.get_or_create_token()

        assert token == "pre-existing-real-token-value"
        assert was_created is False  # migrated, not freshly generated
        assert (tmp_path / ".elly_token").read_text().strip() == "pre-existing-real-token-value"

    def test_generates_a_new_token_when_nothing_old_exists(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
        token, was_created = auth.get_or_create_token()
        assert was_created is True
        assert len(token) == 64

    def test_prefers_an_existing_elly_token_over_a_stale_kx_one(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(auth, "get_data_dir", lambda: tmp_path)
        (tmp_path / ".elly_token").write_text("current-token")
        (tmp_path / ".kx_token").write_text("stale-old-token")

        token, was_created = auth.get_or_create_token()
        assert token == "current-token"
        assert was_created is False


class TestEncryptionKeyMigration:
    def test_carries_an_existing_kx_key_forward_and_content_stays_decryptable(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate a pre-rebrand install: encrypt something under the
        # "old" key file name directly.
        monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
        from cryptography.fernet import Fernet
        real_key = Fernet.generate_key()
        (tmp_path / ".kx_dbkey").write_text(real_key.decode())
        ciphertext = Fernet(real_key).encrypt(b"a real diary entry").decode()

        # Now go through the post-rebrand code path exactly as the app
        # would on first launch after upgrading.
        migrated_key = crypto.get_or_create_key()

        assert migrated_key == real_key
        assert (tmp_path / ".elly_dbkey").read_text().strip() == real_key.decode()
        # The whole point: previously-encrypted content must still
        # decrypt correctly after the migration.
        assert crypto.decrypt_text(ciphertext) == "a real diary entry"

    def test_generates_a_new_key_when_nothing_old_exists(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(crypto, "get_data_dir", lambda: tmp_path)
        key = crypto.get_or_create_key()
        assert key is not None
        assert (tmp_path / ".elly_dbkey").exists()
