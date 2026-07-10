"""Persistence for known-face embeddings, backed by a Turso database."""

import struct
from datetime import datetime, timezone
from pathlib import Path

import turso

from .config import EMBEDDING_DIM

_EMBEDDING = struct.Struct(f"<{EMBEDDING_DIM}f")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS faces (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at TEXT NOT NULL
)
"""


class StoreError(Exception):
    """The face database on disk is unusable and must not be overwritten."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FaceStore:
    """Rows of ``(name, embedding)`` where embeddings are float32 blobs."""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.conn = turso.connect(str(self.path))
            cursor = self.conn.cursor()
            cursor.execute(_SCHEMA)
            self.conn.commit()
            self.known = self._load(cursor)
        except turso.Error as exc:
            raise StoreError(
                f"Could not open face database {self.path} ({exc}). "
                "Fix or move the file, then restart."
            )

    def _load(self, cursor):
        cursor.execute("SELECT id, name, embedding FROM faces ORDER BY id")
        known = []
        for row_id, name, blob in cursor.fetchall():
            if not isinstance(name, str) or not isinstance(blob, bytes) or len(blob) != _EMBEDDING.size:
                raise StoreError(
                    f"{self.path} row {row_id} does not hold a string name and "
                    f"a {EMBEDDING_DIM}-dimensional float32 embedding."
                )
            known.append({"name": name, "embedding": list(_EMBEDDING.unpack(blob))})
        return known

    def append(self, name, embedding):
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT INTO faces (name, embedding, created_at) VALUES (?, ?, ?)",
                (name, _EMBEDDING.pack(*embedding), _now()),
            )
            self.conn.commit()
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")
        self.known.append({"name": name, "embedding": list(embedding)})

    def close(self):
        self.conn.close()
