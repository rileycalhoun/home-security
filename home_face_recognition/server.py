"""HTTP server exposing the recognizer to the browser UI."""

import argparse
import json
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
    SCAN_EVERY_MS,
)
from .storage import FaceStore, StoreError

if TYPE_CHECKING:
    from .recognition import Recognizer

PACKAGE_DIR = Path(__file__).parent


class PayloadTooLarge(Exception):
    pass


def render_index():
    html = (PACKAGE_DIR / "templates" / "index.html").read_text(encoding="utf-8")
    return html.replace("{{ scan_interval_ms }}", str(SCAN_EVERY_MS)).encode()


def load_static_assets():
    # Assets are read once at startup from a fixed allowlist, so arbitrary
    # paths can never reach the filesystem.
    static_dir = PACKAGE_DIR / "static"
    return {
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
    index_html = b""
    static_assets = {}

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send_bytes(HTTPStatus.OK, self.index_html, "text/html; charset=utf-8")
        elif path in self.static_assets:
            body, content_type = self.static_assets[path]
            self.send_bytes(HTTPStatus.OK, body, content_type)
        elif path == "/health":
            self.send_json({"ok": True})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/scan":
                self.send_json(self.recognizer.scan(body))
            elif path == "/save":
                self.send_json(self.recognizer.save(self.parse_name(body)))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
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

    @staticmethod
    def parse_name(body):
        try:
            data = json.loads(body.decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must be JSON.")
        if not isinstance(data, dict) or not isinstance(data.get("name", ""), str):
            raise ValueError("Expected a JSON object with a string 'name'.")
        return data.get("name", "")

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

    Handler.recognizer = Recognizer(store)
    Handler.index_html = render_index()
    Handler.static_assets = load_static_assets()

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        raise SystemExit(f"error: could not bind {args.host}:{args.port} ({exc})")

    print(f"Known faces: {len(store.known)}")
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
