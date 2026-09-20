"""Reusable SQLAlchemy column types with cross-dialect variants.

Production targets PostgreSQL 16. Tests run against SQLite (aiosqlite) for
speed. PostgreSQL-specific types (JSONB, ARRAY, native UUID) get explicit
SQLite fallbacks via SQLAlchemy's `Type.with_variant`.

Both dialects:
- Primary keys: native UUID, default uuid4() in Python.
- Timestamps: timezone-aware DateTime.
- JSON: JSONB on PG, JSON on SQLite.

Production-only types (no SQLite fallback needed in tests):
- ARRAY(Text) for storage_slots.allowed_categories. Used only in storage
  tests; if a test needs to assert on allowed_categories content, do so via
  SQLAlchemy attribute access (Python list), not raw SQL.
"""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import JSON, DateTime, Text
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import Uuid

# Primary key type: native UUID with Python-side uuid4() default. The Alembic
# migration adds `gen_random_uuid()` server-side for PG.
GUID = Uuid


TZDateTime = DateTime(timezone=True)


# JSONB with SQLite fallback to JSON. Stored as TEXT under SQLite but accessed
# as Python dict/list transparently.
JSONBCompat = JSONB().with_variant(JSON(), "sqlite")


# Array of text with SQLite fallback to JSON-encoded list. On SQLite, ARRAY
# columns are stored as TEXT containing JSON; reads return native list.
TextArray = PG_ARRAY(Text).with_variant(JSON(), "sqlite")


# Lightweight helper for Alembic / CHECK constraints: turn a Python Enum into
# its raw string values for use in `sa.text(...)` constraint bodies.
def enum_values(enum_cls: type[Enum]) -> list[str]:
    return [m.value for m in enum_cls]


__all__ = [
    "GUID",
    "JSONBCompat",
    "TZDateTime",
    "TextArray",
    "enum_values",
    "uuid",
]
