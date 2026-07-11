"""End-to-end tests of authentication and the /api/v1 routes over real HTTP."""

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from typing import Any, Dict, Literal, Optional, Tuple, cast, overload

import pytest

from home_face_recognition.config import EMBEDDING_DIM, SETTINGS_SPEC
from home_face_recognition.server import Handler, load_static_assets
from home_face_recognition.storage import FaceStore


class FakeRecognizer:
    def scan(self, _body):
        return {"detected": 0, "faces": [], "known": 0}


def embedding(seed=0.0):
    return [seed + i / EMBEDDING_DIM for i in range(EMBEDDING_DIM)]


@overload
def request(
    port: int,
    method: str,
    path: str,
    body: Any = None,
    headers: Optional[Dict[str, str]] = None,
    *,
    include_headers: Literal[False] = False,
) -> Tuple[int, Any]: ...


@overload
def request(
    port: int,
    method: str,
    path: str,
    body: Any = None,
    headers: Optional[Dict[str, str]] = None,
    *,
    include_headers: Literal[True],
) -> Tuple[int, Any, Dict[str, str]]: ...


def request(port, method, path, body=None, headers=None, *, include_headers=False):
    conn = HTTPConnection("127.0.0.1", port)
    payload = json.dumps(body) if body is not None else None
    request_headers = dict(headers or {})
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    conn.request(method, path, payload, request_headers)
    response = conn.getresponse()
    raw = response.read()
    try:
        result = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        result = raw
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    conn.close()
    if include_headers:
        return response.status, result, response_headers
    return response.status, result


@pytest.fixture
def fresh_server(tmp_path):
    store = FaceStore(tmp_path / "known_faces.db")
    Handler.store = store
    Handler.lock = threading.Lock()
    Handler.static_assets = load_static_assets()
    Handler.recognizer = cast(Any, FakeRecognizer())
    Handler.secure_cookies = False
    Handler.login_failures = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], store
    httpd.shutdown()
    httpd.server_close()
    store.close()


@pytest.fixture
def server(fresh_server):
    port, store = fresh_server
    status, data, headers = request(
        port,
        "POST",
        "/api/v1/auth/setup",
        {"username": "admin", "password": "correct horse battery staple"},
        include_headers=True,
    )
    assert status == 201
    auth = {
        "Cookie": headers["set-cookie"].split(";", 1)[0],
        "X-CSRF-Token": data["csrf_token"],
    }
    yield port, store, auth


def test_first_run_setup_and_session(fresh_server):
    port, _ = fresh_server
    assert request(port, "GET", "/api/v1/auth/status") == (
        200,
        {"setup_required": True, "user": None},
    )
    status, data, headers = request(
        port,
        "POST",
        "/api/v1/auth/setup",
        {"username": "owner", "password": "long local password"},
        include_headers=True,
    )
    assert status == 201 and data["user"]["role"] == "admin"
    assert "HttpOnly" in headers["set-cookie"] and "SameSite=Strict" in headers["set-cookie"]
    cookie = {"Cookie": headers["set-cookie"].split(";", 1)[0]}
    status, current = request(port, "GET", "/api/v1/auth/status", headers=cookie)
    assert status == 200 and current["user"]["username"] == "owner"
    assert request(
        port,
        "POST",
        "/api/v1/auth/setup",
        {"username": "second", "password": "another long password"},
    )[0] == 403


def test_health_static_and_auth_boundary(server):
    port, _, auth = server
    assert request(port, "GET", "/api/v1/health") == (200, {"ok": True})
    assert request(port, "GET", "/")[0] == 200
    assert request(port, "GET", "/static/app.js")[0] == 200
    assert request(port, "GET", "/api/v1/people")[0] == 401
    assert request(port, "GET", "/api/v1/people", headers=auth) == (200, {"people": []})


def test_unknown_routes_are_404(server):
    port, _, auth = server
    assert request(port, "GET", "/api/v1/nope", headers=auth)[0] == 404
    assert request(port, "GET", "/api/v2/people", headers=auth)[0] == 404
    assert request(port, "GET", "/api/v1/people/notanumber", headers=auth)[0] == 404
    assert request(port, "POST", "/api/v1/people", headers=auth)[0] == 404


def test_people_crud(server):
    port, store, auth = server
    person = store.add_embedding("Ada", embedding())
    store.add_embedding("Ada", embedding(0.5))

    status, data = request(port, "GET", "/api/v1/people", headers=auth)
    assert status == 200
    assert [(p["name"], p["embedding_count"]) for p in data["people"]] == [("Ada", 2)]

    status, detail = request(port, "GET", f"/api/v1/people/{person['id']}", headers=auth)
    assert status == 200 and len(detail["embeddings"]) == 2
    status, renamed = request(
        port,
        "PATCH",
        f"/api/v1/people/{person['id']}",
        {"name": "Ada Lovelace"},
        auth,
    )
    assert status == 200 and renamed["name"] == "Ada Lovelace"

    embedding_id = detail["embeddings"][0]["id"]
    assert request(
        port,
        "DELETE",
        f"/api/v1/people/{person['id']}/embeddings/{embedding_id}",
        headers=auth,
    )[0] == 200
    assert request(port, "DELETE", f"/api/v1/people/{person['id']}", headers=auth)[0] == 200
    assert store.people() == []


def test_settings_validation_and_csrf(server):
    port, _, auth = server
    status, data = request(port, "GET", "/api/v1/settings", headers=auth)
    assert status == 200
    assert data["settings"] == {key: spec["default"] for key, spec in SETTINGS_SPEC.items()}
    cookie_only = {"Cookie": auth["Cookie"]}
    assert request(
        port, "PUT", "/api/v1/settings", {"match_distance": 0.6}, cookie_only
    )[0] == 403
    assert request(
        port, "PUT", "/api/v1/settings", {"match_distance": 0.6}, auth
    )[1]["settings"]["match_distance"] == 0.6
    assert request(
        port, "PUT", "/api/v1/settings", {"match_distance": 99}, auth
    )[0] == 400


def test_admin_manages_users_and_viewer_is_restricted(server):
    port, _, auth = server
    status, viewer = request(
        port,
        "POST",
        "/api/v1/users",
        {"username": "watcher", "password": "viewer password 123", "role": "viewer"},
        auth,
    )
    assert status == 201
    status, login, headers = request(
        port,
        "POST",
        "/api/v1/auth/login",
        {"username": "watcher", "password": "viewer password 123"},
        include_headers=True,
    )
    viewer_auth = {
        "Cookie": headers["set-cookie"].split(";", 1)[0],
        "X-CSRF-Token": login["csrf_token"],
    }
    assert status == 200
    assert request(port, "POST", "/api/v1/scan", headers=viewer_auth)[0] == 200
    assert request(port, "GET", "/api/v1/people", headers=viewer_auth)[0] == 403
    assert request(port, "GET", "/api/v1/users", headers=viewer_auth)[0] == 403

    assert request(
        port, "PATCH", f"/api/v1/users/{viewer['id']}", {"enabled": False}, auth
    )[0] == 200
    assert request(port, "GET", "/api/v1/auth/status", headers=viewer_auth)[1]["user"] is None


def test_admin_cannot_remove_own_or_last_admin_access(server):
    port, _, auth = server
    users = request(port, "GET", "/api/v1/users", headers=auth)[1]["users"]
    admin_id = users[0]["id"]
    assert request(
        port, "PATCH", f"/api/v1/users/{admin_id}", {"role": "viewer"}, auth
    )[0] == 400
    assert request(port, "DELETE", f"/api/v1/users/{admin_id}", headers=auth)[0] == 400


def test_login_locks_after_five_failures(server):
    port, _, _ = server
    for attempt in range(5):
        status, _ = request(
            port,
            "POST",
            "/api/v1/auth/login",
            {"username": "admin", "password": "wrong password here"},
        )
        assert status == (429 if attempt == 4 else 401)
    assert request(
        port,
        "POST",
        "/api/v1/auth/login",
        {"username": "admin", "password": "correct horse battery staple"},
    )[0] == 429
