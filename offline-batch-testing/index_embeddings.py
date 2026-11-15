#!/usr/bin/env python3
"""Embed available playlist clips with MERT-v2-30s and store them in Chroma.

Each record is keyed by its playlist video number. The label is the video title.
Similarity between two videos is the cosine similarity of their stored vectors.

    .venv/bin/python index_embeddings.py build
    .venv/bin/python index_embeddings.py similarity 3 5
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import chromadb
import numpy as np
from chromadb.config import Settings

ROOT = Path(__file__).resolve().parent
AUDIO_DIR = ROOT / "audio_clips_30s"
TITLES_PATH = ROOT / "playlist_titles.tsv"
CHROMA_DIR = ROOT / "chroma"
COLLECTION_NAME = "mert_v2_30s"
EMBED_URL = "http://127.0.0.1:8088/embeddings"
MODEL_NAME = "m-a-p/MERT-v2-30s"


def load_titles(path: Path) -> dict[int, tuple[str, str]]:
    titles: dict[int, tuple[str, str]] = {}
    if not path.exists():
        return titles
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\\t") if "\\t" in line else line.split("\t")
        if len(parts) < 3:
            continue
        index = int(parts[0])
        video_id = parts[1]
        title = parts[2].strip()
        titles[index] = (video_id, title)
    return titles


def clip_records() -> list[dict]:
    titles = load_titles(TITLES_PATH)
    records = []
    for path in sorted(AUDIO_DIR.glob("*.wav")):
        index_text, video_id = path.stem.split("_", 1)
        playlist_index = int(index_text)
        listed_id, title = titles.get(playlist_index, (video_id, ""))
        if listed_id != video_id:
            title = ""
        if not title or title == "NA":
            title = video_id
        records.append(
            {
                "key": str(playlist_index),
                "playlist_index": playlist_index,
                "video_id": video_id,
                "label": title,
                "filename": path.name,
                "path": path,
            }
        )
    return records


def embed_wav(path: Path) -> list[float]:
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="audio"; filename="{path.name}"\r\n'.encode(),
            b"Content-Type: audio/wav\r\n\r\n",
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = Request(
        EMBED_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.load(response)
            break
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Embedding failed for {path.name}: {last_error}") from last_error
    vector = payload["embedding"]
    if len(vector) != 1024:
        raise RuntimeError(f"{path.name} returned dimension {len(vector)}, expected 1024.")
    return vector


def collection():
    client = chromadb.PersistentClient(
        path=str(CHROMA_DIR),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine", "model": MODEL_NAME},
    )


def build() -> int:
    records = clip_records()
    if not records:
        print(f"No wav clips found in {AUDIO_DIR}", file=sys.stderr)
        return 1
    store = collection()
    existing = set(store.get(include=[])["ids"])
    pending = [record for record in records if record["key"] not in existing]
    print(
        f"{len(existing)} already indexed, {len(pending)} remaining, {len(records)} clips total.",
        flush=True,
    )
    failures = []
    for number, record in enumerate(pending, start=1):
        try:
            vector = embed_wav(record["path"])
        except Exception as exc:
            failures.append((record["key"], str(exc)))
            print(f"[{number}/{len(pending)}] skipped {record['key']}: {exc}", flush=True)
            continue
        store.upsert(
            ids=[record["key"]],
            embeddings=[vector],
            metadatas=[
                {
                    "playlist_index": record["playlist_index"],
                    "video_id": record["video_id"],
                    "label": record["label"],
                    "filename": record["filename"],
                    "model": MODEL_NAME,
                }
            ],
        )
        print(
            f"[{number}/{len(pending)}] {record['key']}: {record['label']}",
            flush=True,
        )
    print(f"Indexed {store.count()} videos in {CHROMA_DIR}", flush=True)
    if failures:
        print(f"{len(failures)} clips failed.", file=sys.stderr)
        return 1
    return 0


def lookup(store, video_number: str) -> tuple[str, np.ndarray, str]:
    key = str(int(video_number))
    found = store.get(ids=[key], include=["embeddings", "metadatas"])
    if not found["ids"]:
        raise SystemExit(f"Video {key} is not in the index.")
    vector = np.asarray(found["embeddings"][0], dtype=np.float64)
    label = found["metadatas"][0]["label"]
    return key, vector, label


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(left, right) / denominator)


def similarity(left_number: str, right_number: str) -> int:
    store = collection()
    left_key, left_vector, left_label = lookup(store, left_number)
    right_key, right_vector, right_label = lookup(store, right_number)
    score = cosine(left_vector, right_vector)
    print(f"{left_key}: {left_label}")
    print(f"{right_key}: {right_label}")
    print(f"cosine_similarity {score:.6f}")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "build"
    if command == "build":
        return build()
    if command == "similarity":
        if len(sys.argv) != 4:
            print("Usage: index_embeddings.py similarity VIDEO_A VIDEO_B", file=sys.stderr)
            return 2
        return similarity(sys.argv[2], sys.argv[3])
    print("Usage: index_embeddings.py [build|similarity VIDEO_A VIDEO_B]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
