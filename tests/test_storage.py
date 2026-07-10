import pytest

from home_face_recognition.config import EMBEDDING_DIM
from home_face_recognition.storage import FaceStore, StoreError


def test_missing_file_starts_empty(tmp_path):
    store = FaceStore(tmp_path / "known_faces.db")
    assert store.known == []
    store.close()


def test_append_roundtrip(tmp_path):
    path = tmp_path / "known_faces.db"
    embedding = [float(i) / EMBEDDING_DIM for i in range(EMBEDDING_DIM)]
    store = FaceStore(path)
    store.append("Ada", embedding)
    store.close()

    reloaded = FaceStore(path)
    assert [item["name"] for item in reloaded.known] == ["Ada"]
    # Embeddings survive the float32 round trip within precision.
    assert reloaded.known[0]["embedding"] == pytest.approx(embedding, abs=1e-6)
    reloaded.close()


def test_corrupt_database_is_rejected(tmp_path):
    path = tmp_path / "known_faces.db"
    path.write_bytes(b"this is not a database")
    with pytest.raises(StoreError):
        FaceStore(path)
    assert path.read_bytes() == b"this is not a database"
