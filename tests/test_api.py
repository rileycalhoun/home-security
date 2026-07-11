"""End-to-end tests of the /api/v1 routes over real HTTP (no torch needed)."""

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from typing import Any, Tuple

import pytest

from home_face_recognition.config import EMBEDDING_DIM, SETTINGS_SPEC
from home_face_recognition.server import Handler, load_static_assets
from home_face_recognition.storage import FaceStore


def embedding(seed=0.0):
    return [seed + i / EMBEDDING_DIM for i in range(EMBEDDING_DIM)]


@pytest.fixture
def server(tmp_path):
    store = FaceStore(tmp_path / "known_faces.db")
    Handler.store = store
    Handler.lock = threading.Lock()
    Handler.static_assets = load_static_assets()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], store
    httpd.shutdown()
    httpd.server_close()
    store.close()


def request(port, method, path, body=None) -> Tuple[int, Any]:
    conn = HTTPConnection("127.0.0.1", port)
    payload = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    conn.request(method, path, payload, headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    try:
        return response.status, json.loads(raw)
    except json.JSONDecodeError:
        return response.status, raw


def test_health(server):
    port, _ = server
    assert request(port, "GET", "/api/v1/health") == (200, {"ok": True})


def test_index_and_static_are_served(server):
    port, _ = server
    status, body = request(port, "GET", "/")
    assert status == 200 and b"<!doctype html>" in body
    status, _ = request(port, "GET", "/static/app.js")
    assert status == 200


def test_unknown_routes_are_404(server):
    port, _ = server
    assert request(port, "GET", "/api/v1/nope")[0] == 404
    assert request(port, "GET", "/api/v2/people")[0] == 404
    assert request(port, "GET", "/api/v1/people/notanumber")[0] == 404
    assert request(port, "POST", "/api/v1/people")[0] == 404


def test_people_crud(server):
    port, store = server
    assert request(port, "GET", "/api/v1/people") == (200, {"people": []})

    person = store.add_embedding("Ada", embedding())
    store.add_embedding("Ada", embedding(0.5))

    status, data = request(port, "GET", "/api/v1/people")
    assert status == 200
    assert [(p["name"], p["embedding_count"]) for p in data["people"]] == [("Ada", 2)]

    status, detail = request(port, "GET", f"/api/v1/people/{person['id']}")
    assert status == 200
    assert len(detail["embeddings"]) == 2

    status, renamed = request(
        port, "PATCH", f"/api/v1/people/{person['id']}", {"name": "Ada Lovelace"}
    )
    assert status == 200
    assert renamed["name"] == "Ada Lovelace"

    embedding_id = detail["embeddings"][0]["id"]
    status, _ = request(
        port, "DELETE", f"/api/v1/people/{person['id']}/embeddings/{embedding_id}"
    )
    assert status == 200
    assert len(store.known) == 1

    status, _ = request(port, "DELETE", f"/api/v1/people/{person['id']}")
    assert status == 200
    assert store.people() == []
    assert request(port, "DELETE", f"/api/v1/people/{person['id']}")[0] == 404


def test_rename_collision_is_400(server):
    port, store = server
    store.add_embedding("Ada", embedding())
    person = store.add_embedding("Grace", embedding(0.5))
    status, data = request(port, "PATCH", f"/api/v1/people/{person['id']}", {"name": "ada"})
    assert status == 400
    assert "already exists" in data["error"]


def test_rename_missing_person_is_404(server):
    port, _ = server
    assert request(port, "PATCH", "/api/v1/people/9999", {"name": "Ghost"})[0] == 404


def test_settings_roundtrip(server):
    port, _ = server
    status, data = request(port, "GET", "/api/v1/settings")
    assert status == 200
    assert data["settings"] == {key: spec["default"] for key, spec in SETTINGS_SPEC.items()}
    assert data["spec"]["match_distance"]["label"]

    status, data = request(port, "PUT", "/api/v1/settings", {"match_distance": 0.6})
    assert status == 200
    assert data["settings"]["match_distance"] == 0.6

    status, data = request(port, "PUT", "/api/v1/settings", {"match_distance": 99})
    assert status == 400
    assert "between" in data["error"]

    status, data = request(port, "PUT", "/api/v1/settings", {"bogus": 1})
    assert status == 400
