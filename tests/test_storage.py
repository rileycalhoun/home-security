import struct

import pytest
import turso

from home_face_recognition.config import EMBEDDING_DIM, SETTINGS_SPEC
from home_face_recognition.storage import FaceStore, StoreError


def embedding(seed=0.0):
    return [seed + i / EMBEDDING_DIM for i in range(EMBEDDING_DIM)]


@pytest.fixture
def store(tmp_path):
    store = FaceStore(tmp_path / "known_faces.db")
    yield store
    store.close()


def test_missing_file_starts_empty(store):
    assert store.known == []
    assert store.people() == []
    assert store.person_count() == 0


def test_enroll_roundtrip(tmp_path):
    path = tmp_path / "known_faces.db"
    store = FaceStore(path)
    person = store.add_embedding("Ada", embedding())
    assert person["name"] == "Ada"
    assert person["embedding_count"] == 1
    store.close()

    reloaded = FaceStore(path)
    assert [entry["name"] for entry in reloaded.known] == ["Ada"]
    # Embeddings survive the float32 round trip within precision.
    assert reloaded.known[0]["embedding"] == pytest.approx(embedding(), abs=1e-6)
    reloaded.close()


def test_same_name_groups_embeddings_case_insensitively(store):
    first = store.add_embedding("Ada", embedding())
    second = store.add_embedding("  ada ", embedding(0.5))
    assert second["id"] == first["id"]
    assert second["embedding_count"] == 2
    assert store.person_count() == 1
    assert len(store.known) == 2


def test_person_detail_lists_embeddings(store):
    person = store.add_embedding("Ada", embedding())
    store.add_embedding("Ada", embedding(0.5))
    detail = store.person(person["id"])
    assert detail is not None
    assert detail["name"] == "Ada"
    assert len(detail["embeddings"]) == 2
    assert store.person(9999) is None


def test_rename_person(store):
    person = store.add_embedding("Ada", embedding())
    renamed = store.rename_person(person["id"], "Ada Lovelace")
    assert renamed is not None
    assert renamed["name"] == "Ada Lovelace"
    assert store.known[0]["name"] == "Ada Lovelace"
    assert store.rename_person(9999, "Nobody") is None


def test_rename_collision_is_rejected(store):
    store.add_embedding("Ada", embedding())
    person = store.add_embedding("Grace", embedding(0.5))
    with pytest.raises(ValueError):
        store.rename_person(person["id"], "ADA")


def test_rename_rejects_empty_name(store):
    person = store.add_embedding("Ada", embedding())
    with pytest.raises(ValueError):
        store.rename_person(person["id"], "   ")


def test_delete_person_removes_embeddings(store):
    person = store.add_embedding("Ada", embedding())
    store.add_embedding("Ada", embedding(0.5))
    assert store.delete_person(person["id"]) is True
    assert store.known == []
    assert store.people() == []
    assert store.delete_person(person["id"]) is False


def test_delete_single_embedding(store):
    person = store.add_embedding("Ada", embedding())
    store.add_embedding("Ada", embedding(0.5))
    detail = store.person(person["id"])
    assert detail is not None
    first_id = detail["embeddings"][0]["id"]
    assert store.delete_embedding(person["id"], first_id) is True
    assert len(store.known) == 1
    # Repeating the delete, or using the wrong person, is a miss.
    assert store.delete_embedding(person["id"], first_id) is False
    assert store.delete_embedding(9999, detail["embeddings"][1]["id"]) is False


def test_revision_bumps_on_change(store):
    before = store.revision
    person = store.add_embedding("Ada", embedding())
    assert store.revision > before
    before = store.revision
    store.delete_person(person["id"])
    assert store.revision > before


def test_settings_default_and_roundtrip(tmp_path):
    path = tmp_path / "known_faces.db"
    store = FaceStore(path)
    assert store.settings == {key: spec["default"] for key, spec in SETTINGS_SPEC.items()}
    updated = store.save_settings({"match_distance": 0.5, "scan_interval_ms": 1000})
    assert updated["match_distance"] == 0.5
    assert updated["scan_interval_ms"] == 1000
    store.close()

    reloaded = FaceStore(path)
    assert reloaded.settings["match_distance"] == 0.5
    assert reloaded.settings["scan_interval_ms"] == 1000
    # Untouched keys keep their defaults.
    assert (
        reloaded.settings["min_face_probability"]
        == SETTINGS_SPEC["min_face_probability"]["default"]
    )
    reloaded.close()


def test_settings_validation(store):
    with pytest.raises(ValueError):
        store.save_settings({"nonsense": 1})
    with pytest.raises(ValueError):
        store.save_settings({"match_distance": 99})
    with pytest.raises(ValueError):
        store.save_settings({"scan_interval_ms": "fast"})
    # A failed save leaves settings untouched.
    assert store.settings["match_distance"] == SETTINGS_SPEC["match_distance"]["default"]


def test_users_and_sessions_survive_reopen(tmp_path):
    path = tmp_path / "known_faces.db"
    store = FaceStore(path)
    user = store.create_user("owner", "a sufficiently long password", "admin")
    token, _ = store.create_session(user["id"])
    store.close()

    reloaded = FaceStore(path)
    authenticated = reloaded.authenticate("OWNER", "a sufficiently long password")
    session = reloaded.session(token)
    assert authenticated is not None and authenticated["id"] == user["id"]
    assert session is not None and session["username"] == "owner"
    reloaded.close()


def test_migrates_v1_schema(tmp_path):
    path = tmp_path / "known_faces.db"
    packer = struct.Struct(f"<{EMBEDDING_DIM}f")
    conn = turso.connect(str(path))
    cursor = conn.cursor()
    cursor.execute(
        "CREATE TABLE faces (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "embedding BLOB NOT NULL, created_at TEXT NOT NULL)"
    )
    for name, seed, created in [
        ("Ada", 0.0, "2026-01-01T00:00:00+00:00"),
        ("Ada", 0.5, "2026-01-02T00:00:00+00:00"),
        ("Grace", 1.0, "2026-01-03T00:00:00+00:00"),
    ]:
        cursor.execute(
            "INSERT INTO faces (name, embedding, created_at) VALUES (?, ?, ?)",
            (name, packer.pack(*embedding(seed)), created),
        )
    conn.commit()
    conn.close()

    store = FaceStore(path)
    people = store.people()
    assert [(person["name"], person["embedding_count"]) for person in people] == [
        ("Ada", 2),
        ("Grace", 1),
    ]
    # Original enrollment timestamps survive the migration.
    assert people[0]["created_at"] == "2026-01-01T00:00:00+00:00"
    assert [entry["name"] for entry in store.known] == ["Ada", "Ada", "Grace"]
    assert store.known[1]["embedding"] == pytest.approx(embedding(0.5), abs=1e-6)
    store.close()

    # The old table is gone, so reopening does not migrate twice.
    reloaded = FaceStore(path)
    assert len(reloaded.known) == 3
    reloaded.close()


def test_corrupt_database_is_rejected(tmp_path):
    path = tmp_path / "known_faces.db"
    path.write_bytes(b"this is not a database")
    with pytest.raises(StoreError):
        FaceStore(path)
    assert path.read_bytes() == b"this is not a database"
