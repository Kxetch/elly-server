"""SQLAlchemy column types that transparently encrypt/decrypt values.

Using these as a column's type means every other part of the codebase
(domain functions, `model_to_dict`, the ORM in general) reads and
writes plain Python strings/dicts exactly as before -- encryption
happens automatically at the SQLAlchemy layer on the way in/out of the
database. The one thing this breaks: SQL-level pattern matching
(`ilike`, etc.) directly against an encrypted column no longer works,
since the database only ever sees ciphertext. See
domain/notes.py::search_notes and domain/memory.py::recall for how
those specific call sites adapted (decrypt-then-filter in Python
instead of a SQL WHERE clause) -- see domain/crypto.py for the "why
field-level, not whole-database" rationale.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.types import Text, TypeDecorator

from elly_server.domain.crypto import decrypt_text, encrypt_text


class EncryptedText(TypeDecorator):
    """A Text column whose value is transparently encrypted at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Optional[str], dialect: Any) -> Optional[str]:
        if value is None:
            return None
        return encrypt_text(value)

    def process_result_value(self, value: Optional[str], dialect: Any) -> Optional[str]:
        if value is None:
            return None
        return decrypt_text(value)


class EncryptedJSON(TypeDecorator):
    """A JSON-valued column whose serialized form is transparently
    encrypted at rest (stored as encrypted text, not plain JSON)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Optional[str]:
        if value is None:
            return None
        return encrypt_text(json.dumps(value))

    def process_result_value(self, value: Optional[str], dialect: Any) -> Any:
        if value is None:
            return None
        return json.loads(decrypt_text(value))
