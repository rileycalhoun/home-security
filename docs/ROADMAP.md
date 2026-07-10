# Roadmap

From a single-camera face recognition demo to a local-first home security
system. This document is the plan of record; items move up or down as
priorities change, but the guiding principles don't.

## Guiding principles

- **Local-first, always.** No cloud services, no external auth providers, no
  telemetry. Everything — video, embeddings, credentials, events — stays on
  hardware you own. If a feature can't work without the internet, it doesn't
  belong here.
- **Privacy by default.** Face embeddings are biometric data. They are never
  committed, never leave the machine, and anything new that touches them
  (backups, events, snapshots) must justify its retention.
- **Security features must fail safe.** A face match can *unlock* convenience
  features, but anything with physical consequences (locks, doors) needs
  explicit opt-in and defense against spoofing.
- **Small, inspectable stack.** Prefer the standard library and small
  dependencies over frameworks; the whole system should stay readable in an
  afternoon.

## Where we are (v0.1)

- Browser webcam scanning against a local recognition server
- MTCNN detection + FaceNet embeddings, nearest-neighbor matching
- One-click enrollment, Turso database storage
- Single camera, single page, no auth

---

## v0.2 — Dashboard foundation & face management

Turn the single page into the skeleton of a real dashboard — the surface
everything else plugs into.

- [ ] Dashboard layout: sidebar navigation (Cameras, People, Events, Settings),
      responsive down to phone width
- [ ] **People page**: list enrolled faces with name, enrollment count, and
      date; rename and delete entries
- [ ] Multiple embeddings per person (enroll several angles under one name;
      match against all of them) with per-person management
- [ ] Enrollment flow with live preview and quality feedback ("face too small",
      "too dark")
- [ ] Settings page exposing the tuning knobs (`MATCH_DISTANCE`, scan interval,
      detection confidence) with persistence in the database
- [ ] REST API cleanup: versioned JSON API (`/api/v1/...`) so the dashboard and
      future integrations share one interface

## v0.3 — Local authentication

Pure local-first auth. No Supabase, no Firebase, no OAuth round-trips.

- [ ] First-run setup: when no users exist, the dashboard walks through
      creating the initial admin account
- [ ] Username + password login with a battle-tested KDF (argon2 or scrypt);
      credentials stored in the same Turso database
- [ ] Server-side sessions with HttpOnly, SameSite cookies; CSRF protection on
      mutating endpoints
- [ ] User management in the dashboard: admins create, disable, and delete
      users
- [ ] Roles: **admin** (manage users, cameras, people, settings) and **viewer**
      (watch streams, see events)
- [ ] Rate limiting / lockout on failed logins
- [ ] Optional TLS (self-signed or user-provided certs) so credentials never
      cross the LAN in plaintext — required before the server binds to
      anything other than localhost

## v0.4 — Multi-camera & server-side ingestion

The architectural turn: today the browser pushes webcam frames *to* the
server. Monitoring IP cameras means the server pulls streams itself and the
browser becomes a pure viewer.

- [ ] Camera abstraction: a camera is a named, configurable source with its own
      scan schedule and enabled/disabled state
- [ ] RTSP camera support (OpenCV/ffmpeg ingestion) — covers most IP cameras
- [ ] Keep browser-webcam as just another camera type (useful for laptops and
      quick tests)
- [ ] Camera management in the dashboard: add, edit, remove, reorder
- [ ] **Single view**: full-size view of one camera with instant switching
- [ ] **Grid view**: watch multiple cameras at once, with recognition overlays
      on every tile
- [ ] Efficient streaming to the browser (MJPEG or WebSocket frames) with
      per-tile detection status
- [ ] Per-camera health: connection state, FPS, last-seen indicators, automatic
      reconnection with backoff

## v0.5 — Home Assistant integration

Any camera hooked up to a Home Assistant instance becomes monitorable from
the dashboard.

- [ ] Connect to a Home Assistant instance (URL + long-lived access token,
      stored locally)
- [ ] Discover HA camera entities and import them as cameras (snapshot polling
      first, stream proxy where available)
- [ ] Publish recognition results back to HA as events/sensors
      (`person_detected`, `unknown_face_detected`, per-person presence), so HA
      automations can react
- [ ] MQTT support as a lighter-weight alternative transport
- [ ] Ship as an installable HA add-on for Home Assistant OS users

## v0.6 — Events, notifications & recordings

A security system is only as good as its memory.

- [ ] **Event log**: every recognition (who, which camera, when, confidence)
      recorded in the database with a snapshot thumbnail
- [ ] Events page: filterable timeline (per camera, per person,
      unknown-faces-only, date range)
- [ ] Configurable retention policy — auto-prune old events and snapshots
- [ ] Notifications on configurable triggers (unknown face, specific person,
      camera offline) via local-friendly channels: ntfy, webhooks, MQTT, and
      HA notifications
- [ ] Event-triggered clip recording (N seconds around a detection) with
      storage caps
- [ ] Arm/disarm modes (home / away / night) that change which triggers fire
- [ ] Backup & restore: export/import the database (faces, users, settings)
      from the dashboard

## v0.7 — Automations & smart devices

The dashboard grows from "cameras" into "everything in the system."

- [ ] Device registry: smart locks, lights, sirens — driven through Home
      Assistant services or MQTT rather than per-vendor SDKs
- [ ] Rule builder: "when *unknown face* on *front door camera* while *armed*,
      then *turn on porch light + send notification*"
- [ ] Smart lock actions with mandatory safeguards: multi-frame confirmation,
      minimum confidence threshold, per-lock opt-in, full audit trail in the
      event log
- [ ] Liveness / anti-spoofing checks (a photo of Riley must not open
      anything) — prerequisite for any unlock automation
- [ ] Presence dashboard: who is currently home, based on recent recognitions

## v1.0 — Hardening & polish

- [ ] Performance: ONNX / CoreML / CUDA execution paths, frame batching across
      cameras, motion-gated scanning so idle cameras cost ~nothing
- [ ] Deployment: Dockerfile + compose file, systemd unit, documented
      Raspberry Pi / mini-PC setups
- [ ] Database migrations: versioned schema with automatic upgrades
- [ ] CI on GitHub Actions (tests, lint), tagged releases with changelogs
- [ ] Installable PWA so the dashboard feels native on phones and wall-mounted
      tablets
- [ ] Documentation: setup guides, API reference, architecture overview,
      troubleshooting

## Ideas parked for later

- Face database encryption at rest (passphrase-derived key)
- Pet / package / vehicle detection alongside faces
- Doorbell integrations (two-way audio)
- Multi-node setups: lightweight capture agents on remote Pis feeding one
  recognition server (note: Turso holds an exclusive file lock, so a single
  writer process owns the database — remote nodes talk to it over the API)
- Hardware acceleration on edge TPUs (Coral)

---

Suggestions and PRs against this roadmap are welcome — open an issue to
discuss reordering or additions.
