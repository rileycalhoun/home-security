"""Tunable defaults for the recognizer and server."""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
DEFAULT_DB_FILENAME = "known_faces.db"

# Frames are downscaled to this width before detection to keep scans fast.
DETECTION_WIDTH = 360

# Output dimension of InceptionResnetV1; stored embeddings must match.
EMBEDDING_DIM = 512

# Reject enrollment if the newest scan is older than this — the face on
# screen may no longer be the face that was detected.
SAVE_MAX_AGE_SECONDS = 3.0
MAX_NAME_LENGTH = 64
# A 640px JPEG frame is ~100 KB; anything near this limit is not a frame.
MAX_BODY_BYTES = 8 * 1024 * 1024

# Enrollment quality gates: embeddings from tiny or underexposed crops match
# poorly, so refuse to store them and tell the user how to fix the shot.
# Detection alone finds faces down to ~0.11 of the frame width, so this must
# sit well above that for the "too small" feedback to ever fire.
MIN_ENROLL_FACE_FRACTION = 0.18  # face width / frame width
MIN_ENROLL_BRIGHTNESS = 60.0  # mean gray level of the face crop (0-255)

# Runtime-tunable settings, persisted in the database and edited on the
# Settings page. "kind" is a JSON-safe type tag ("int"/"float") because the
# spec is served to the dashboard as-is.
SETTINGS_SPEC = {
    "match_distance": {
        "kind": "float",
        "default": 0.8,
        "min": 0.1,
        "max": 2.0,
        "step": 0.05,
        "label": "Match distance",
        "help": (
            "Embedding distance below which a face counts as a match. "
            "Raise it if the camera misses people it should know, lower it "
            "if it confuses similar faces."
        ),
    },
    "scan_interval_ms": {
        "kind": "int",
        "default": 450,
        "min": 100,
        "max": 5000,
        "step": 50,
        "label": "Scan interval (ms)",
        "help": "How often the browser posts a frame for scanning. Lower is smoother but costs more CPU.",
    },
    "min_face_probability": {
        "kind": "float",
        "default": 0.90,
        "min": 0.5,
        "max": 0.99,
        "step": 0.01,
        "label": "Detection confidence",
        "help": "Detections below this confidence are discarded. Lower it if real faces go unnoticed.",
    },
}
