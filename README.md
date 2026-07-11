# Home Face Recognition

Local, browser-based face recognition — the first building block of a DIY home
security system. A small dashboard shows your webcam feed scanned in real
time: known faces are labeled in green, unknown faces in amber. Enroll people
from the camera view, manage them on the People page, and tune recognition on
the Settings page. Everything runs on your machine: no cloud, no accounts, no
telemetry.

## How it works

- The browser captures webcam frames and posts a downscaled JPEG to the local
  server on a configurable interval (450 ms by default).
- [MTCNN](https://github.com/timesler/facenet-pytorch) detects faces, and an
  InceptionResnetV1 (FaceNet, pretrained on VGGFace2) turns each face into a
  512-dimensional embedding.
- Each embedding is compared against every enrolled embedding by Euclidean
  distance; anything within the match-distance threshold is labeled with that
  person's name. A person can have several enrollments (different angles,
  lighting) and matches against all of them.
- People, their embeddings, and settings live in `known_faces.db`, a
  [Turso](https://github.com/tursodatabase/turso) database (SQLite-compatible)
  next to where you run the server. Databases from v0.1 are migrated in place
  on first start.
- The dashboard talks to a versioned JSON API under `/api/v1/` (people CRUD,
  scan, enroll, settings) that future integrations share.

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Python 3.9–3.11 (pinned by
`torch==2.2.2`).

```sh
uv run home-face-recognition
```

Then open <http://localhost:3000>. The first run downloads PyTorch and about
110 MB of model weights, so it takes a while; after that startup is a few
seconds.

To enroll someone, wait until their face is boxed, click **Enroll this face**,
and type a name — live quality feedback warns when the face is too small or
the shot is too dark. Enrolling the same name a few times (different angles,
lighting) improves recognition; the People page lists everyone with their
enrollments, and lets you rename or delete them.

### Options

| Flag | Default | Purpose |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Bind address. Keep it on localhost unless you know what you're doing — there is no authentication. |
| `--port` | `3000` | HTTP port. |
| `--db` | `./known_faces.db` | Where face embeddings are stored (Turso database). |

### Tuning

The recognition knobs live on the dashboard's **Settings** page and persist in
the database:

- **Match distance** — lower if it confuses similar faces, higher if it misses
  people it should know.
- **Detection confidence** — minimum detector confidence to count as a face.
- **Scan interval** — trade responsiveness for CPU.

Lower-level defaults (detection width, enrollment quality thresholds) live in
`home_face_recognition/config.py`.

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

This is the recognition core of what will grow into a full local-first home
security system: a management dashboard, local authentication, multi-camera
monitoring, Home Assistant integration, event logging with notifications, and
smart device automations. The full plan lives in
[docs/ROADMAP.md](docs/ROADMAP.md).
