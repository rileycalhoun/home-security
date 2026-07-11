"""HTTP server exposing the dashboard and the versioned JSON API."""

import argparse
import json
import threading
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from urllib.parse import urlparse

from .config import (
    DEFAULT_DB_FILENAME,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_BODY_BYTES,
    SETTINGS_SPEC,
)
from .storage import FaceStore, StoreError

if TYPE_CHECKING:
    from .recognition import Recognizer

PACKAGE_DIR = Path(__file__).parent


class PayloadTooLarge(Exception):
    pass


class NotFound(Exception):
    pass


def load_static_assets():
    # Assets are read once at startup from a fixed allowlist, so arbitrary
    # paths can never reach the filesystem.
    static_dir = PACKAGE_DIR / "static"
    return {
        "/": (
            (PACKAGE_DIR / "templates" / "index.html").read_bytes(),
            "text/html; charset=utf-8",
        ),
        "/static/style.css": (
            (static_dir / "style.css").read_bytes(),
            "text/css; charset=utf-8",
        ),
        "/static/app.js": (
            (static_dir / "app.js").read_bytes(),
            "text/javascript; charset=utf-8",
        ),
    }


class Handler(BaseHTTPRequestHandler):
    recognizer: ClassVar["Recognizer"]
    store: ClassVar[FaceStore]
    lock: ClassVar[threading.Lock] = threading.Lock()
    static_assets: ClassVar[dict] = {}

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/"):
                self.handle_api(method, path)
            elif method == "GET" and path in self.static_assets:
                body, content_type = self.static_assets[path]
                self.send_bytes(HTTPStatus.OK, body, content_type)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except NotFound:
            self.send_json({"error": "Not found."}, HTTPStatus.NOT_FOUND)
        except PayloadTooLarge:
            # The unread body would be misparsed as the next keep-alive
            # request, so drop the connection after responding.
            self.close_connection = True
            self.send_json(
                {"error": "Request body too large."},
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception:
            traceback.print_exc()
            self.send_json(
                {"error": "Internal server error."},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def handle_api(self, method, path):
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3 or segments[1] != "v1":
            raise NotFound()
        resource = segments[2:]

        if resource == ["health"] and method == "GET":
            return self.send_json({"ok": True})

        if resource == ["scan"] and method == "POST":
            return self.send_json(self.recognizer.scan(self.read_body()))

        if resource == ["enroll"] and method == "POST":
            name = self.read_json().get("name", "")
            if not isinstance(name, str):
                raise ValueError("Expected a JSON object with a string 'name'.")
            return self.send_json(self.recognizer.enroll(name))

        if resource == ["people"] and method == "GET":
            with self.lock:
                people = self.store.people()
            return self.send_json({"people": people})

        if len(resource) == 2 and resource[0] == "people":
            return self.handle_person(method, self.parse_id(resource[1]))

        if (
            len(resource) == 4
            and resource[0] == "people"
            and resource[2] == "embeddings"
            and method == "DELETE"
        ):
            person_id = self.parse_id(resource[1])
            embedding_id = self.parse_id(resource[3])
            with self.lock:
                deleted = self.store.delete_embedding(person_id, embedding_id)
            if not deleted:
                raise NotFound()
            return self.send_json({"deleted": True})

        if resource == ["settings"]:
            if method == "GET":
                with self.lock:
                    settings = dict(self.store.settings)
                return self.send_json({"settings": settings, "spec": SETTINGS_SPEC})
            if method == "PUT":
                updates = self.read_json()
                with self.lock:
                    settings = self.store.save_settings(updates)
                return self.send_json({"settings": settings})

        raise NotFound()

    def handle_person(self, method, person_id):
        if method == "GET":
            with self.lock:
                person = self.store.person(person_id)
            if person is None:
                raise NotFound()
            return self.send_json(person)
        if method == "PATCH":
            name = self.read_json().get("name", "")
            if not isinstance(name, str):
                raise ValueError("Expected a JSON object with a string 'name'.")
            with self.lock:
                person = self.store.rename_person(person_id, name)
            if person is None:
                raise NotFound()
            return self.send_json(person)
        if method == "DELETE":
            with self.lock:
                deleted = self.store.delete_person(person_id)
            if not deleted:
                raise NotFound()
            return self.send_json({"deleted": True})
        raise NotFound()

    @staticmethod
    def parse_id(segment):
        try:
            return int(segment)
        except ValueError:
            raise NotFound()

    def read_json(self):
        try:
            data = json.loads(self.read_body().decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must be JSON.")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length header.")
        if length < 0:
            raise ValueError("Invalid Content-Length header.")
        if length > MAX_BODY_BYTES:
            raise PayloadTooLarge()
        return self.rfile.read(length)

    def send_json(self, payload, status=HTTPStatus.OK):
        self.send_bytes(status, json.dumps(payload).encode(), "application/json")

    def send_bytes(self, status, body, content_type):
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client went away mid-response (e.g. tab closed); nothing to do.

    def log_message(self, format, *args):
        return


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="home-face-recognition",
        description="Local, browser-based face recognition.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="bind address (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default: %(default)s)")
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_FILENAME,
        help="path to the face database (default: %(default)s in the current directory)",
    )
    args = parser.parse_args(argv)

    try:
        store = FaceStore(args.db)
    except StoreError as exc:
        raise SystemExit(f"error: {exc}")

    print("Loading models (first run downloads ~110 MB of weights)...")
    from .recognition import Recognizer  # deferred: importing torch/facenet is slow

    Handler.store = store
    Handler.recognizer = Recognizer(store, Handler.lock)
    Handler.static_assets = load_static_assets()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"error: could not bind {args.host}:{args.port} ({exc})")

    print(f"Known people: {store.person_count()}")
    print(f"Open http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()
