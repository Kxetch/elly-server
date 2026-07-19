"""Turn SQLAlchemy model instances into plain JSON-friendly dicts.

MCP tool/resource functions return these directly -- FastMCP handles
the JSON encoding, so this just needs to produce primitives (str,
int, float, bool, list, dict, None).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import inspect as sa_inspect


def model_to_dict(obj: Any) -> dict[str, Any]:
    mapper = sa_inspect(obj).mapper
    result: dict[str, Any] = {}
    for column in mapper.columns:
        value = getattr(obj, column.key)
        if isinstance(value, datetime):
            value = value.isoformat()
        result[column.key] = value
    return result
