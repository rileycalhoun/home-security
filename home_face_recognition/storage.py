"""Persistence for people, face embeddings, and settings, backed by a Turso database."""

import hashlib
import hmac
import secrets
import struct
import time
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
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash BLOB NOT NULL,
        password_salt BLOB NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin', 'viewer')),
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS sessions (
        token_hash BLOB PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        csrf_token TEXT NOT NULL,
        expires_at INTEGER NOT NULL
    )""",
]

_PERSON_SUMMARY = (
    "SELECT people.id, people.name, people.created_at, "
    "COUNT(embeddings.id), MAX(embeddings.created_at) "
    "FROM people LEFT JOIN embeddings ON embeddings.person_id = people.id "
)

_KINDS = {"int": int, "float": float}
_ROLES = {"admin", "viewer"}
_SCRYPT_N = 1 << 14
_SESSION_SECONDS = 7 * 24 * 60 * 60


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

    # -- authentication --------------------------------------------------

    def user_count(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

    def users(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, username, role, enabled, created_at FROM users ORDER BY lower(username)"
        )
        return [self._user_row(row) for row in cursor.fetchall()]

    def create_user(self, username, password, role):
        username, password, role = self._valid_credentials(username, password, role)
        salt = secrets.token_bytes(16)
        password_hash = self._hash_password(password, salt)
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cursor.fetchone() is not None:
                raise ValueError("Username already exists.")
            cursor.execute(
                "INSERT INTO users (username, password_hash, password_salt, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, password_hash, salt, role, _now()),
            )
            self.conn.commit()
            cursor.execute(
                "SELECT id, username, role, enabled, created_at FROM users WHERE username = ?",
                (username,),
            )
            return self._user_row(cursor.fetchone())
        except turso.Error as exc:
            raise StoreError(f"Could not write to {self.path}: {exc}")

    def authenticate(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        if len(username) > 64 or len(password) > 1024:
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT id, username, password_hash, password_salt, role, enabled, created_at "
            "FROM users WHERE username = ?",
            (username.strip(),),
        )
        row = cursor.fetchone()
        if row is None:
            # Keep unknown-user attempts expensive enough to resist enumeration.
            self._hash_password(password, b"\0" * 16)
            return None
        user_id, name, expected, salt, role, enabled, created_at = row
        actual = self._hash_password(password, salt)
        if not enabled or not hmac.compare_digest(expected, actual):
            return None
        return self._user_row((user_id, name, role, enabled, created_at))

    def create_session(self, user_id):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        expires_at = int(time.time()) + _SESSION_SECONDS
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(time.time()),))
        cursor.execute(
            "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
            (self._token_hash(token), user_id, csrf, expires_at),
        )
        self.conn.commit()
        return token, csrf

    def session(self, token):
        if not token:
            return None
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT users.id, users.username, users.role, users.enabled, users.created_at, "
            "sessions.csrf_token FROM sessions JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.enabled = 1",
            (self._token_hash(token), int(time.time())),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        user = self._user_row(row[:5])
        user["csrf_token"] = row[5]
        return user

    def delete_session(self, token):
        if not token:
            return
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM sessions WHERE token_hash = ?", (self._token_hash(token),))
        self.conn.commit()

    def update_user(self, user_id, updates, current_user_id):
        unknown = set(updates) - {"role", "enabled"}
        if unknown or not updates:
            raise ValueError("Expected 'role' or 'enabled'.")
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, username, role, enabled, created_at FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        role = updates.get("role", row[2])
        enabled = updates.get("enabled", bool(row[3]))
        if role not in _ROLES or not isinstance(enabled, bool):
            raise ValueError("Role must be admin or viewer and enabled must be true or false.")
        if user_id == current_user_id and (role != "admin" or not enabled):
            raise ValueError("You cannot remove your own admin access.")
        if row[2] == "admin" and row[3] and (role != "admin" or not enabled):
            self._require_another_admin(cursor, user_id)
        cursor.execute("UPDATE users SET role = ?, enabled = ? WHERE id = ?", (role, int(enabled), user_id))
        if not enabled:
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.conn.commit()
        return self._user_row((row[0], row[1], role, int(enabled), row[4]))

    def delete_user(self, user_id, current_user_id):
        if user_id == current_user_id:
            raise ValueError("You cannot delete your own account.")
        cursor = self.conn.cursor()
        cursor.execute("SELECT role, enabled FROM users WHERE id = ?", (user_id,))
        row = cursor.fetchone()
        if row is None:
            return False
        if row[0] == "admin" and row[1]:
            self._require_another_admin(cursor, user_id)
        cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.conn.commit()
        return True

    @staticmethod
    def _hash_password(password, salt):
        return hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=8, p=1, dklen=32)

    @staticmethod
    def _token_hash(token):
        return hashlib.sha256(token.encode()).digest()

    @staticmethod
    def _user_row(row):
        return {
            "id": row[0],
            "username": row[1],
            "role": row[2],
            "enabled": bool(row[3]),
            "created_at": row[4],
        }

    @staticmethod
    def _valid_credentials(username, password, role):
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError("Username and password must be strings.")
        username = username.strip()
        if not 1 <= len(username) <= 64 or any(char.isspace() for char in username):
            raise ValueError("Username must be 1-64 characters with no spaces.")
        if len(password) < 12:
            raise ValueError("Password must be at least 12 characters.")
        if len(password) > 1024:
            raise ValueError("Password is too long.")
        if role not in _ROLES:
            raise ValueError("Role must be admin or viewer.")
        return username, password, role

    @staticmethod
    def _require_another_admin(cursor, user_id):
        cursor.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND enabled = 1 AND id != ?", (user_id,)
        )
        if cursor.fetchone() is None:
            raise ValueError("At least one enabled admin is required.")

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
