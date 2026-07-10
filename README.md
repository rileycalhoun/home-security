# Home Face Recognition

Local, browser-based face recognition — the first building block of a DIY home
security system. Your webcam feed is scanned in real time, known faces are
labeled in green, unknown faces in amber, and you can enroll new faces with one
click. Everything runs on your machine: no cloud, no accounts, no telemetry.

## How it works

- The browser captures webcam frames and posts a downscaled JPEG to the local
  server every ~450 ms.
- [MTCNN](https://github.com/timesler/facenet-pytorch) detects faces, and an
  InceptionResnetV1 (FaceNet, pretrained on VGGFace2) turns each face into a
  512-dimensional embedding.
- Each embedding is compared against the saved database by Euclidean distance;
  anything within `MATCH_DISTANCE` of a saved face is labeled with that name.
- Saved faces live in `known_faces.db`, a [Turso](https://github.com/tursodatabase/turso)
  database (SQLite-compatible) next to where you run the server.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.9–3.11 (pinned by
`torch==2.2.2`).

```sh
uv run home-face-recognition
```

Then open <http://localhost:3000>. The first run downloads PyTorch and about
110 MB of model weights, so it takes a while; after that startup is a few
seconds.

To enroll someone, wait until their face is boxed, click **Save this face**,
and type a name. Saving the same person a few times (different angles,
lighting) improves recognition.

### Options

| Flag | Default | Purpose |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address. Keep it on localhost unless you know what you're doing — there is no authentication. |
| `--port` | `3000` | HTTP port. |
| `--db` | `./known_faces.db` | Where face embeddings are stored (Turso database). |

### Tuning

Recognition knobs live in `home_face_recognition/config.py`:

- `MATCH_DISTANCE` — lower if it confuses similar faces, higher if it misses
  people it should know.
- `MIN_FACE_PROBABILITY` — minimum detector confidence to count as a face.
- `SCAN_EVERY_MS` / `DETECTION_WIDTH` — trade accuracy for CPU.

## Privacy

`known_faces.db` contains face embeddings — biometric data. It is
`.gitignore`d and should never be committed or shared. The server binds to
localhost by default and camera frames never leave your machine.

## Development

```sh
uv run pytest
```

## License

[GPL-2.0](LICENSE)

## Roadmap

This is the recognition core of what will grow into a full home security
system: multiple/IP cameras, event logging, notifications on unknown faces,
and a management UI for the face database.
