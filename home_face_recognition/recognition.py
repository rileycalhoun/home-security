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
    MIN_FACE_PROBABILITY,
    SAVE_MAX_AGE_SECONDS,
)
from .matching import area, known_tensor, match_embedding


class Recognizer:
    def __init__(self, store):
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
        self.last_faces = []
        self.last_scan_at = 0.0
        self.lock = threading.Lock()

    def scan(self, jpeg_bytes):
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode image.")

        with self.lock:
            faces = self._scan_frame(frame)
            self.last_faces = faces
            self.last_scan_at = time.monotonic()
            known_count = len(self.store.known)

        return {
            "faces": [
                {
                    "box": face["box"],
                    "name": face["name"],
                    "distance": face["distance"],
                }
                for face in faces
            ],
            "detected": len(faces),
            "known": known_count,
        }

    def _scan_frame(self, frame):
        original_h, original_w = frame.shape[:2]
        scale = min(1.0, DETECTION_WIDTH / original_w)
        if scale < 1.0:
            frame = cv2.resize(frame, (DETECTION_WIDTH, int(original_h * scale)))
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        boxes, probs = self.mtcnn.detect(image)
        if boxes is None:
            return []

        # Drop low-confidence detections before running the (expensive) resnet.
        keep = [
            i
            for i, prob in enumerate(probs)
            if prob is not None and prob >= MIN_FACE_PROBABILITY
        ]
        if not keep:
            return []
        boxes = boxes[keep]

        faces = self.mtcnn.extract(image, boxes, None)
        if faces is None:
            return []
        with torch.inference_mode():
            embeddings = F.normalize(self.resnet(faces.to(self.device)), dim=1).cpu()

        found = []
        for box, embedding in zip(boxes, embeddings):
            name, distance = match_embedding(
                embedding, self.store.known, self.known_embeddings
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
                }
            )
        return found

    def save(self, name):
        with self.lock:
            if not self.last_faces:
                raise ValueError("No face detected yet.")
            if time.monotonic() - self.last_scan_at > SAVE_MAX_AGE_SECONDS:
                raise ValueError(
                    "The last detection is stale — keep your face in view and try again."
                )
            face = max(self.last_faces, key=lambda item: area(item["box"]))
            name = name.strip()[:MAX_NAME_LENGTH] or f"Person {len(self.store.known) + 1}"
            self.store.append(name, face["embedding"].tolist())
            self.known_embeddings = known_tensor(self.store.known)
            return {
                "status": f"Saved {name}.",
                "known": len(self.store.known),
            }
