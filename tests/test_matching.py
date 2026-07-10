import torch

from home_face_recognition.matching import area, known_tensor, match_embedding

KNOWN = [
    {"name": "Ada", "embedding": [1.0, 0.0]},
    {"name": "Grace", "embedding": [0.0, 1.0]},
]


def test_matches_closest_known_face():
    embeddings = known_tensor(KNOWN)
    name, distance = match_embedding(torch.tensor([0.99, 0.01]), KNOWN, embeddings, 0.2)
    assert name == "Ada"
    assert distance is not None and distance < 0.2


def test_distant_face_is_unknown():
    embeddings = known_tensor(KNOWN)
    name, distance = match_embedding(torch.tensor([-1.0, 0.0]), KNOWN, embeddings, 0.2)
    assert name == "Unknown"
    assert distance is not None and distance > 0.2


def test_empty_database_is_unknown():
    assert match_embedding(torch.tensor([1.0, 0.0]), [], known_tensor([])) == ("Unknown", None)


def test_area():
    assert area([0, 0, 4, 3]) == 12
    assert area([4, 3, 0, 0]) == 0  # degenerate boxes never win max()
