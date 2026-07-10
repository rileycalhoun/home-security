"""Tunable defaults for the recognizer and server."""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3000
DEFAULT_DB_FILENAME = "known_faces.db"

# Frames are downscaled to this width before detection to keep scans fast.
DETECTION_WIDTH = 360
# How often the browser posts a frame for scanning.
SCAN_EVERY_MS = 450
# Detections below this MTCNN confidence are discarded.
MIN_FACE_PROBABILITY = 0.90
# Tune lower if it misses you, higher if it confuses similar faces.
MATCH_DISTANCE = 0.8

# Output dimension of InceptionResnetV1; stored embeddings must match.
EMBEDDING_DIM = 512

# Reject /save if the newest scan is older than this — the face on screen
# may no longer be the face that was detected.
SAVE_MAX_AGE_SECONDS = 3.0
MAX_NAME_LENGTH = 64
# A 640px JPEG frame is ~100 KB; anything near this limit is not a frame.
MAX_BODY_BYTES = 8 * 1024 * 1024
