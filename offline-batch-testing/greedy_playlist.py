#!/usr/bin/env python3
"""Build a distance matrix from the MERT index and walk a greedy playlist.

Distance is 1 minus cosine similarity. From the starting playlist number, the
walk repeatedly appends the closest video that has not been visited.

    .venv/bin/python greedy_playlist.py 67

Writes adjacency_matrix.npz and greedy_path.txt next to this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import chromadb
import numpy as np
from chromadb.config import Settings

ROOT = Path(__file__).resolve().parent
CHROMA_DIR = ROOT / "chroma"
COLLECTION_NAME = "mert_v2_30s"
MATRIX_PATH = ROOT / "adjacency_matrix.npz"
PATH_PATH = ROOT / "greedy_path.txt"


def load_index() -> tuple[np.ndarray, np.ndarray, list[str]]:
    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    store = client.get_collection(COLLECTION_NAME)
    found = store.get(include=["embeddings", "metadatas"])
    if not found["ids"]:
        raise SystemExit(f"No embeddings in {CHROMA_DIR}.")
    indexes = np.asarray([int(video_id) for video_id in found["ids"]], dtype=np.int32)
    order = np.argsort(indexes, kind="mergesort")
    indexes = indexes[order]
    vectors = np.asarray(found["embeddings"], dtype=np.float64)[order]
    labels = [found["metadatas"][int(position)]["label"] for position in order]
    return indexes, vectors, labels


def distance_matrix(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    similarity = (vectors / norms) @ (vectors / norms).T
    similarity = np.clip(similarity, -1.0, 1.0)
    distance = 1.0 - similarity
    np.fill_diagonal(distance, 0.0)
    return distance.astype(np.float32)


def greedy_path(indexes: np.ndarray, distance: np.ndarray, start: int) -> tuple[np.ndarray, np.ndarray]:
    position = {int(video): slot for slot, video in enumerate(indexes.tolist())}
    if start not in position:
        raise SystemExit(f"Video {start} is not in the index.")
    count = len(indexes)
    visited = np.zeros(count, dtype=bool)
    order = np.empty(count, dtype=np.int32)
    hops = np.zeros(count, dtype=np.float32)
    current = position[start]
    for step in range(count):
        visited[current] = True
        order[step] = indexes[current]
        if step == count - 1:
            break
        row = distance[current].copy()
        row[visited] = np.inf
        nxt = int(np.argmin(row))
        hops[step + 1] = row[nxt]
        current = nxt
    return order, hops


def write_path(indexes: np.ndarray, labels: list[str], order: np.ndarray, hops: np.ndarray) -> None:
    label_by_index = {int(video): label for video, label in zip(indexes.tolist(), labels)}
    lines = ["step\tvideo\tdistance_from_previous\tlabel"]
    for step, video in enumerate(order.tolist()):
        distance = "0" if step == 0 else f"{hops[step]:.6f}"
        lines.append(f"{step}\t{video}\t{distance}\t{label_by_index[video]}")
    PATH_PATH.write_text("\n".join(lines) + "\n")


def main() -> int:
    indexes, vectors, labels = load_index()
    start = int(sys.argv[1]) if len(sys.argv) > 1 else int(indexes[0])
    distance = distance_matrix(vectors)
    order, hops = greedy_path(indexes, distance, start)
    np.savez(MATRIX_PATH, video_indexes=indexes, distance=distance)
    write_path(indexes, labels, order, hops)
    print(f"Saved {distance.shape[0]}x{distance.shape[1]} distance matrix to {MATRIX_PATH}")
    print(f"Saved greedy path of {len(order)} videos, starting at {start}, to {PATH_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
