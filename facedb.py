"""Known-face database: enrollment storage plus cosine-similarity matching.

Layout on disk (all inside ./faces):

    people.json      {"version":1,"people":{"Alice":[[...128 floats...], ...]}}

Each person can hold many sample embeddings. Enrolling 10-20 varied samples per
person (different angles, distances, lighting) matters far more than any tuning
knob elsewhere in this project -- a single enrolment photo gives a database that
misidentifies people constantly.

All matching is local. No image or embedding ever leaves the machine except the
single annotated snapshot you explicitly choose to be notified about.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

UNKNOWN = "Unknown"


def l2_normalise(vec: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vec))
    if norm < 1e-9:
        return vec.astype(np.float32)
    return (vec / norm).astype(np.float32)


class FaceDatabase:
    """Stores reference embeddings and matches live ones against them."""

    def __init__(self, path: str, threshold: float = 0.363) -> None:
        self.path = path
        self.threshold = float(threshold)
        self._people: dict[str, list[np.ndarray]] = {}
        self.load()

    # ---------------------------------------------------------------- storage

    def load(self) -> None:
        if not os.path.exists(self.path):
            self._people = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                blob = json.load(fh)
        except (json.JSONDecodeError, OSError):
            # Never let a truncated write take the whole watcher down.
            self._people = {}
            return

        people: dict[str, list[np.ndarray]] = {}
        for name, samples in (blob.get("people") or {}).items():
            vecs = []
            for sample in samples:
                arr = np.asarray(sample, dtype=np.float32)
                if arr.shape == (128,):
                    vecs.append(l2_normalise(arr))
            if vecs:
                people[name] = vecs
        self._people = people

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "version": 1,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "people": {
                name: [np.asarray(v, dtype=np.float32).round(6).tolist() for v in vecs]
                for name, vecs in sorted(self._people.items())
            },
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------- enrollment

    def add(self, name: str, embedding: np.ndarray) -> None:
        vec = l2_normalise(np.asarray(embedding, dtype=np.float32).reshape(-1))
        if vec.shape != (128,):
            raise ValueError(f"expected a 128-d embedding, got {vec.shape}")
        self._people.setdefault(name.strip(), []).append(vec)

    def remove(self, name: str) -> int:
        removed = len(self._people.pop(name.strip(), []))
        if removed:
            self.save()
        return removed

    @property
    def names(self) -> list[str]:
        return sorted(self._people)

    def sample_counts(self) -> dict[str, int]:
        return {name: len(vecs) for name, vecs in sorted(self._people.items())}

    def __len__(self) -> int:
        return len(self._people)

    # ---------------------------------------------------------------- matching

    def match(self, embedding: np.ndarray) -> tuple[str, float]:
        """Return (name, score). Name is UNKNOWN when nothing clears the threshold."""
        if not self._people:
            return UNKNOWN, 0.0

        probe = l2_normalise(np.asarray(embedding, dtype=np.float32).reshape(-1))
        best_name, best_score = UNKNOWN, -1.0
        for name, vecs in self._people.items():
            # Reference set as one matrix -> single matvec per person.
            scores = np.stack(vecs) @ probe
            top = float(scores.max())
            if top > best_score:
                best_name, best_score = name, top

        if best_score < self.threshold:
            return UNKNOWN, best_score
        return best_name, best_score


def infer_name_from_path(path: str) -> str:
    """'photos/Alice_01.jpg' -> 'Alice'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for sep in ("_", "-"):
        if sep in stem:
            head = stem.split(sep)[0].strip()
            if head:
                return head
    return stem.strip()
