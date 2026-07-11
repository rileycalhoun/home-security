"""Face detection and recognition on top of MTCNN + InceptionResnetV1."""

import threading
import time

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from facenet_pytorch import InceptionResnetV1, MTCNN
from PIL import Image

from .config import (
    DETECTION_WIDTH,
    MAX_NAME_LENGTH,
    MIN_ENROLL_BRIGHTNESS,
    MIN_ENROLL_FACE_FRACTION,
    SAVE_MAX_AGE_SECONDS,
)
from .matching import area, known_tensor, match_embedding


class Recognizer:
    def __init__(self, store, lock=None):
        self.device = torch.device("cpu")
        self.mtcnn = MTCNN(
            image_size=160,
            margin=20,
            keep_all=True,
            min_face_size=40,
            device=self.device,
        )
        self.resnet = InceptionResnetV1(pretrained="vggface2", device=self.device).eval()
        self.store = store
        self.known_embeddings = known_tensor(store.known)
        self.known_revision = store.revision
        self.last_faces = []
        self.last_scan_at = 0.0
        # Shared with the HTTP handlers so scans never race store mutations.
        self.lock = lock or threading.Lock()

    def _refresh_known(self):
        if self.known_revision != self.store.revision:
            self.known_embeddings = known_tensor(self.store.known)
            self.known_revision = self.store.revision

    def scan(self, jpeg_bytes):
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode image.")

        with self.lock:
            self._refresh_known()
            faces = self._scan_frame(frame)
            self.last_faces = faces
            self.last_scan_at = time.monotonic()
            known_count = self.store.person_count()

        return {
            "faces": [
                {
                    "box": face["box"],
                    "name": face["name"],
                    "distance": face["distance"],
                    "quality": {"ok": not face["issues"], "issues": face["issues"]},
                }
                for face in faces
            ],
            "detected": len(faces),
            "known": known_count,
        }

    def _scan_frame(self, frame):
        settings = self.store.settings
        original_h, original_w = frame.shape[:2]
        scale = min(1.0, DETECTION_WIDTH / original_w)
        if scale < 1.0:
            frame = cv2.resize(frame, (DETECTION_WIDTH, int(original_h * scale)))
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        boxes, probs, *_ = self.mtcnn.detect(image)
        if boxes is None:
            return []

        # Drop low-confidence detections before running the (expensive) resnet.
        keep = [
            i
            for i, prob in enumerate(probs)
            if prob is not None and prob >= settings["min_face_probability"]
        ]
        if not keep:
            return []
        boxes = boxes[keep]

        faces = self.mtcnn.extract(image, boxes, None)
        if faces is None:
            return []
        with torch.inference_mode():
            embeddings = F.normalize(self.resnet(faces.to(self.device)), dim=1).cpu()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        found = []
        for box, embedding in zip(boxes, embeddings):
            name, distance = match_embedding(
                embedding, self.store.known, self.known_embeddings, settings["match_distance"]
            )
            x1, y1, x2, y2 = (float(v) for v in (box / scale))
            found.append(
                {
                    "box": [
                        min(max(x1, 0.0), original_w),
                        min(max(y1, 0.0), original_h),
                        min(max(x2, 0.0), original_w),
                        min(max(y2, 0.0), original_h),
                    ],
                    "name": name,
                    "distance": distance,
                    "embedding": embedding,
                    "issues": self._quality_issues(gray, box),
                }
            )
        return found

    @staticmethod
    def _quality_issues(gray, box):
        """Human-readable reasons this crop would make a poor enrollment."""
        frame_h, frame_w = gray.shape
        x1 = min(max(int(box[0]), 0), frame_w)
        y1 = min(max(int(box[1]), 0), frame_h)
        x2 = min(max(int(box[2]), 0), frame_w)
        y2 = min(max(int(box[3]), 0), frame_h)
        issues = []
        if (x2 - x1) / frame_w < MIN_ENROLL_FACE_FRACTION:
            issues.append("Face too small — move closer to the camera.")
        crop = gray[y1:y2, x1:x2]
        if crop.size == 0 or float(crop.mean()) < MIN_ENROLL_BRIGHTNESS:
            issues.append("Too dark — add more light.")
        return issues

    def enroll(self, name):
        with self.lock:
            if not self.last_faces:
                raise ValueError("No face detected yet.")
            if time.monotonic() - self.last_scan_at > SAVE_MAX_AGE_SECONDS:
                raise ValueError(
                    "The last detection is stale — keep your face in view and try again."
                )
            face = max(self.last_faces, key=lambda item: area(item["box"]))
            if face["issues"]:
                raise ValueError(" ".join(face["issues"]))
            name = name.strip()[:MAX_NAME_LENGTH] or f"Person {self.store.person_count() + 1}"
            person = self.store.add_embedding(name, face["embedding"].tolist())
            self._refresh_known()
            count = person["embedding_count"]
            return {
                "status": (
                    f"Enrolled {person['name']} "
                    f"({count} enrollment{'' if count == 1 else 's'})."
                ),
                "person": person,
                "known": self.store.person_count(),
            }
