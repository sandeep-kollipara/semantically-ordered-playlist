"""Class centroids built from MERT embeddings. Used by /classify."""

import json
import os
import threading
from pathlib import Path

import numpy as np

_LOCK = threading.Lock()


def _path() -> Path:
    return Path(os.environ.get("CLASS_STORE", "/cache/classes/prototypes.json"))


def _empty() -> dict:
    return {"layer": None, "classes": {}}


def _read() -> dict:
    path = _path()
    if not path.exists():
        return _empty()
    data = json.loads(path.read_text())
    data.setdefault("layer", None)
    data.setdefault("classes", {})
    return data


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def layer_of_store() -> int | None:
    with _LOCK:
        return _read()["layer"]


def list_classes() -> list[dict]:
    with _LOCK:
        data = _read()
        return [
            {"label": label, "examples": entry["count"]}
            for label, entry in sorted(data["classes"].items())
        ]


def add_example(label: str, embedding: np.ndarray, layer: int | None) -> dict:
    label = label.strip()
    if not label:
        raise ValueError("Class label is empty.")
    vector = np.asarray(embedding, dtype=np.float64)
    with _LOCK:
        data = _read()
        if data["classes"] and data["layer"] != layer:
            raise ValueError(
                "Registered classes use a different layer. "
                "Delete them or classify with the same layer."
            )
        data["layer"] = layer
        current = data["classes"].get(label)
        if current is None:
            total = vector
            count = 1
        else:
            total = np.asarray(current["sum"], dtype=np.float64) + vector
            count = int(current["count"]) + 1
        data["classes"][label] = {"count": count, "sum": total.tolist()}
        _write(data)
        return {"label": label, "examples": count, "layer": layer}


def delete_class(label: str) -> bool:
    with _LOCK:
        data = _read()
        if label not in data["classes"]:
            return False
        del data["classes"][label]
        if not data["classes"]:
            data["layer"] = None
        _write(data)
        return True


def centroids(layer: int | None) -> list[tuple[str, np.ndarray]]:
    with _LOCK:
        data = _read()
        if not data["classes"]:
            return []
        if data["layer"] != layer:
            raise ValueError(
                "Registered classes use a different layer. "
                "Classify with the same layer used to register them."
            )
        found = []
        for label, entry in sorted(data["classes"].items()):
            total = np.asarray(entry["sum"], dtype=np.float64)
            found.append((label, total / int(entry["count"])))
        return found


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    left_norm = float(np.linalg.norm(left))
    right_norm = float(np.linalg.norm(right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return float(np.dot(left, right) / (left_norm * right_norm))
