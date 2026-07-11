"""Embedding matching helpers (torch-only, no camera or model dependencies)."""

import torch
import torch.nn.functional as F


def known_tensor(known):
    if not known:
        return torch.empty((0, 512), dtype=torch.float32)
    embeddings = torch.tensor(
        [item["embedding"] for item in known], dtype=torch.float32
    )
    return F.normalize(embeddings, dim=1)


def match_embedding(embedding, known, embeddings, threshold):
    if len(known) == 0:
        return "Unknown", None
    embedding = F.normalize(embedding.float().view(1, -1), dim=1)
    distances = torch.linalg.vector_norm(embeddings - embedding, dim=1)
    best = int(torch.argmin(distances))
    distance = float(distances[best])
    if distance <= threshold:
        return known[best]["name"], distance
    return "Unknown", distance


def area(box):
    x1, y1, x2, y2 = box
    return max(0, x2 - x1) * max(0, y2 - y1)
