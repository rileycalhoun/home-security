"""Persistence for people, face embeddings, and settings, backed by a Turso database."""

import struct
from datetime import datetime, timezone
from pathlib import Path

import turso

from .config import EMBEDDING_DIM, MAX_NAME_LENGTH, SETTINGS_SPEC

_EMBEDDING = struct.Struct(f"<{EMBEDDING_DIM}f")

_TABLES = [
    """CREATE TABLE IF NOT EXISTS people (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS embeddings (
        id INTEGER PRIMARY KEY,
        person_id INTEGER NOT NULL REFERENCES people(id),
        embedding BLOB NOT NULL,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
]

_PERSON_SUMMARY = (
    "SELECT people.id, people.name, people.created_at, "
    "COUNT(embeddings.id), MAX(embeddings.created_at) "
    "FROM people LEFT JOIN embeddings ON embeddings.person_id = people.id "
)

_KINDS = {"int": int, "float": float}


class StoreError(Exception):
    """The face database on disk is unusable and must not be overwritten."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _summary_row(row):
    person_id, name, created_at, count, last_enrolled_at = row
    return {
        "id": person_id,
        "name": name,
        "created_at": created_at,
        "embedding_count": count,
        "last_enrolled_at": last_enrolled_at,
    }


class FaceStore:
    """People and their float32 embedding blobs, plus persisted settings.

    ``known`` mirrors the embeddings table as ``{person_id, name, embedding}``
    dicts for matching; ``revision`` bumps on every change so callers can
    cache derived tensors.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.revision = 0
        try:
            self.conn = turso.connect(str(self.path))
            cursor = self.conn.cursor()
            for statement in _TABLES:
                cursor.execute(statement)
            self._migrate_v1(cursor)
            self.conn.commit()
            self.known = self._load_known(cursor)
            self.settings = self._load_settings(cursor)
        except turso.Error as exc:
            raise StoreError(
                f"Could not open face database {self.path} ({exc}). "
                "Fix or move the file, then restart."
            )

    def _migrate_v1(self, cursor):
        """Fold the flat v0.1 ``faces`` table into people + embeddings."""
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'faces'")
        if cursor.fetchone() is None:
            return
        cursor.execute("SELECT name, embedding, created_at FROM faces ORDER BY id")
        for name, blob, created_at in cursor.fetchall():
            person_id = self._find_or_create_person(cursor, name, created_at)
            cursor.execute(
                "INSERT INTO embeddings (person_id, embedding, created_at) VALUES (?, ?, ?)",
                (person_id, blob, created_at),
            )
        cursor.execute("DROP TABLE faces")

    @staticmethod
    def _find_or_create_person(cursor, name, created_at):
        cursor.execute("SELECT id FROM people WHERE lower(name) = lower(?)", (name,))
        row = cursor.fetchone()
        if row is not None:
            return row[0]
        cursor.execute(
            "INSERT INTO people (name, created_at) VALUES (?, ?) RETURNING id",
            (name, created_at),
        )
        return cursor.fetchone()[0]

    def _load_known(self, cursor):
        cursor.execute(
            "SELECT embeddings.id, people.id, people.name, embeddings.embedding "
            "FROM embeddings JOIN people ON people.id = embeddings.person_id "
            "ORDER BY embeddings.id"
        )
        known = []
        for row_id, person_id, name, blob in cursor.fetchall():
            if not isinstance(name, str) or not isinstance(blob, bytes) or len(blob) != _EMBEDDING.size:
                raise StoreError(
                    f"{self.path} embedding {row_id} does not hold a string name and "
                    f"a {EMBEDDING_DIM}-dimensional float32 embedding."
                )
            known.append(
                {"person_id": person_id, "name": name, "embedding": list(_EMBEDDING.unpack(blob))}
            )
        return known

    def _refresh_known(self, cursor):
        self.known = self._load_known(cursor)
        self.revision += 1

    def person_count(self):
        """People with at least one embedding — those matching can find."""
        return len({entry["person_id"] for entry in self.known})

    # -- people ----------------------------------------------------------

    def people(self):
        cursor = self.conn.cursor()
        cursor.execute(_PERSON_SUMMARY + "GROUP BY people.id ORDER BY lower(people.name)")
        return [_summary_row(row) for row in cursor.fetchall()]

    def person(self, person_id):
        """Full detail for one person, or ``None`` if the id is unknown."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, name, created_at FROM people WHERE id = ?", (person_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        cursor.execute(
            "SELECT id, created_at FROM embeddings WHERE person_id = ? ORDER BY id",
            (person_id,),
        )
        embeddings = [{"id": emb_id, "created_at": created} for emb_id, created in cursor.fetchall()]
        return {
            "id": row[0],
            "name": row[1],
            "created_at": row[2],
            "embedding_count": len(embeddings),
            "embeddings": embeddings,
        }

    def add_embedding(self, name, embedding):
        """Store one embedding under ``name``, creating the person if new."""
        name = self._valid_name(name)
        try:
            cursor = self.conn.cursor()
            person_id = self._find_or_create_person(cursor, name, _now())
            cursor.execute(
                "INSERT INTO embeddings (person_id, embedding, created_at) VALUES (?, ?, ?)",
                (person_id, _EMBEDDING.pack(*embedding), _now()),
            )
            self.conn.commit()
            self._refresh_known(cursor)
            return self._summary(cursor, person_id)
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")

    def rename_person(self, person_id, name):
        """Rename; returns the updated summary or ``None`` if the id is unknown."""
        name = self._valid_name(name)
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM people WHERE id = ?", (person_id,))
            if cursor.fetchone() is None:
                return None
            cursor.execute(
                "SELECT 1 FROM people WHERE lower(name) = lower(?) AND id != ?",
                (name, person_id),
            )
            if cursor.fetchone() is not None:
                raise ValueError(f'Someone named "{name}" already exists.')
            cursor.execute("UPDATE people SET name = ? WHERE id = ?", (name, person_id))
            self.conn.commit()
            self._refresh_known(cursor)
            return self._summary(cursor, person_id)
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")

    def delete_person(self, person_id):
        """Delete a person and all their embeddings; False if the id is unknown."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM people WHERE id = ?", (person_id,))
            if cursor.fetchone() is None:
                return False
            cursor.execute("DELETE FROM embeddings WHERE person_id = ?", (person_id,))
            cursor.execute("DELETE FROM people WHERE id = ?", (person_id,))
            self.conn.commit()
            self._refresh_known(cursor)
            return True
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")

    def delete_embedding(self, person_id, embedding_id):
        """Delete one enrollment; False if it does not belong to the person."""
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT 1 FROM embeddings WHERE id = ? AND person_id = ?",
                (embedding_id, person_id),
            )
            if cursor.fetchone() is None:
                return False
            cursor.execute("DELETE FROM embeddings WHERE id = ?", (embedding_id,))
            self.conn.commit()
            self._refresh_known(cursor)
            return True
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")

    @staticmethod
    def _summary(cursor, person_id):
        cursor.execute(_PERSON_SUMMARY + "WHERE people.id = ? GROUP BY people.id", (person_id,))
        return _summary_row(cursor.fetchone())

    @staticmethod
    def _valid_name(name):
        name = name.strip()
        if not name:
            raise ValueError("Name must not be empty.")
        if len(name) > MAX_NAME_LENGTH:
            raise ValueError(f"Name must be at most {MAX_NAME_LENGTH} characters.")
        return name

    # -- settings ----------------------------------------------------------

    def _load_settings(self, cursor):
        cursor.execute("SELECT key, value FROM settings")
        stored = {key: value for key, value in cursor.fetchall()}
        settings = {}
        for key, spec in SETTINGS_SPEC.items():
            try:
                settings[key] = self._coerce(key, stored[key])
            except (KeyError, ValueError):
                settings[key] = spec["default"]
        return settings

    def save_settings(self, updates):
        """Validate, persist, and apply a partial settings update."""
        unknown = set(updates) - set(SETTINGS_SPEC)
        if unknown:
            raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")
        coerced = {key: self._coerce(key, value) for key, value in updates.items()}
        try:
            cursor = self.conn.cursor()
            for key, value in coerced.items():
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
            self.conn.commit()
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")
        self.settings.update(coerced)
        return dict(self.settings)

    @staticmethod
    def _coerce(key, value):
        spec = SETTINGS_SPEC[key]
        try:
            if isinstance(value, bool):
                raise ValueError
            number = _KINDS[spec["kind"]](value)
        except (TypeError, ValueError):
            raise ValueError(f"{spec['label']} must be a number.")
        if not spec["min"] <= number <= spec["max"]:
            raise ValueError(f"{spec['label']} must be between {spec['min']} and {spec['max']}.")
        return number

    def close(self):
        self.conn.close()
