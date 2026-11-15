"""Build a playlist distance matrix with MERT-v2-30s, then discard the scratch data."""

import json
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import chromadb
import numpy as np
from chromadb.config import Settings

from .playlists import playlist_id_from_url, public_playlist_videos

_REPO = Path(__file__).resolve().parents[2]
_YTDLP = _REPO / "offline-batch-testing" / ".venv" / "bin" / "yt-dlp"
_FFMPEG_DIR = _REPO / "offline-batch-testing" / ".tools" / "bin"
_DEPLOY = _REPO / "mert-v2-container" / "deploy.sh"
_MERT_HEALTH = "http://127.0.0.1:8088/health"
_MERT_EMBED = "http://127.0.0.1:8088/embeddings"
_NODE = shutil.which("node")
_DOWNLOAD_WORKERS = 8
_DOWNLOAD_BATCH = 100
_DOWNLOAD_PAUSE_SECONDS = 10

_jobs = {}
_jobs_lock = threading.Lock()


def start_vectorize(playlist_url):
    playlist_id = playlist_id_from_url(playlist_url)
    if not playlist_id:
        raise ValueError("The playlist URL is invalid.")
    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job["playlist_id"] == playlist_id and job["state"] == "running":
                return job_id
        job_id = uuid.uuid4().hex
        _jobs[job_id] = {
            "state": "running",
            "playlist_id": playlist_id,
            "matrix": None,
            "error": "",
            "progress": {
                "downloaded": 0,
                "download_total": 0,
                "encoded": 0,
                "encode_total": 0,
            },
        }
    threading.Thread(
        target=_run,
        args=(job_id, playlist_url, playlist_id),
        name=f"vectorize-{job_id[:8]}",
        daemon=True,
    ).start()
    return job_id


def vectorize_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        return {
            "state": job["state"],
            "error": job["error"],
            "matrix": job["matrix"],
            "progress": dict(job["progress"]),
        }


def _progress(job_id, **fields):
    with _jobs_lock:
        _jobs[job_id]["progress"].update(fields)


def _run(job_id, playlist_url, playlist_id):
    work = Path(tempfile.mkdtemp(prefix=f"vectorize-{playlist_id[:12]}-"))
    audio_dir = work / "audio"
    chroma_dir = work / "chroma"
    audio_dir.mkdir()
    try:
        clips = _download_clips(job_id, playlist_url, audio_dir)
        if not clips:
            raise RuntimeError("None of the playlist videos had a 30-second clip.")
        _ensure_mert()
        _store_embeddings(job_id, chroma_dir, clips)
        matrix = _matrix_from_chroma(chroma_dir, playlist_id)
        with _jobs_lock:
            _jobs[job_id]["matrix"] = matrix
            _jobs[job_id]["state"] = "done"
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["state"] = "error"
            _jobs[job_id]["error"] = str(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _download_clips(job_id, playlist_url, audio_dir):
    videos = list(public_playlist_videos(playlist_url))
    _progress(job_id, download_total=len(videos))
    clips = []
    numbered = list(enumerate(videos))
    for start in range(0, len(numbered), _DOWNLOAD_BATCH):
        if start:
            time.sleep(_DOWNLOAD_PAUSE_SECONDS)
        batch = numbered[start : start + _DOWNLOAD_BATCH]
        with ThreadPoolExecutor(
            max_workers=_DOWNLOAD_WORKERS,
            thread_name_prefix="vectorize-download",
        ) as pool:
            futures = [
                pool.submit(_download_one, job_id, audio_dir, index, video, clips)
                for index, video in batch
            ]
            for future in futures:
                future.result()
    clips.sort(key=lambda clip: clip["index"])
    return clips


def _download_one(job_id, audio_dir, index, video, clips):
    stem = f"{index:04d}_{video.id}"
    command = [
        str(_YTDLP),
        "--no-playlist",
        "--ignore-errors",
        "--impersonate",
        "chrome",
        "--cookies-from-browser",
        "firefox",
        *(["--js-runtimes", f"node:{_NODE}"] if _NODE else []),
        "--download-sections",
        "*0:00-0:30",
        "-f",
        "ba[abr<=80]/worstaudio/ba",
        "--ffmpeg-location",
        str(_FFMPEG_DIR),
        "-o",
        str(audio_dir / f"{stem}.%(ext)s"),
        f"https://www.youtube.com/watch?v={video.id}",
    ]
    subprocess.run(command, check=False, capture_output=True)
    clip = _saved_clip(audio_dir, stem)
    if clip is None:
        return
    with _jobs_lock:
        clips.append({"index": index, "video_id": video.id, "path": clip})
        _jobs[job_id]["progress"]["downloaded"] = len(clips)


def _saved_clip(audio_dir, stem):
    leftover = {".part", ".ytdl", ".json"}
    matches = [
        path
        for path in audio_dir.glob(f"{stem}.*")
        if path.is_file() and path.suffix.lower() not in leftover
    ]
    return matches[0] if matches else None


def _ensure_mert():
    if _mert_ready():
        return
    if not _DEPLOY.exists():
        raise RuntimeError("The MERT container is not running and its deploy script is missing.")
    completed = subprocess.run([str(_DEPLOY), "start"], check=False)
    if completed.returncode != 0 or not _mert_ready():
        raise RuntimeError("The MERT container did not become ready.")


def _mert_ready():
    try:
        with urlopen(_MERT_HEALTH, timeout=3) as response:
            payload = json.load(response)
    except (OSError, ValueError):
        return False
    return bool(payload.get("ready"))


def _store_embeddings(job_id, chroma_dir, clips):
    client = chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.create_collection(
        name="mert_v2_30s",
        metadata={"hnsw:space": "cosine", "model": "m-a-p/MERT-v2-30s"},
    )
    _progress(job_id, encode_total=len(clips))
    for encoded, clip in enumerate(clips, start=1):
        _progress(job_id, encoded=encoded)
        vector = _embed_clip(clip["path"])
        collection.upsert(
            ids=[str(clip["index"])],
            embeddings=[vector],
            metadatas=[{"playlist_index": clip["index"], "video_id": clip["video_id"]}],
        )


def _embed_clip(path):
    content_type = {
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".opus": "audio/ogg",
        ".ogg": "audio/ogg",
        ".wav": "audio/wav",
    }.get(path.suffix.lower(), "application/octet-stream")
    boundary = uuid.uuid4().hex
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="audio"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = Request(
        _MERT_EMBED,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    last_error = None
    for _attempt in range(3):
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.load(response)
            break
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
    else:
        raise RuntimeError(f"Embedding failed for {path.name}: {last_error}")
    vector = payload["embedding"]
    if len(vector) != 1024:
        raise RuntimeError(f"{path.name} returned dimension {len(vector)}, expected 1024.")
    return vector


def _matrix_from_chroma(chroma_dir, playlist_id):
    client = chromadb.PersistentClient(
        path=str(chroma_dir),
        settings=Settings(anonymized_telemetry=False),
    )
    collection = client.get_collection("mert_v2_30s")
    found = collection.get(include=["embeddings", "metadatas"])
    order = sorted(
        range(len(found["ids"])),
        key=lambda slot: int(found["metadatas"][slot]["playlist_index"]),
    )
    indexes = [int(found["metadatas"][slot]["playlist_index"]) for slot in order]
    video_ids = [found["metadatas"][slot]["video_id"] for slot in order]
    vectors = np.asarray(found["embeddings"], dtype=np.float64)[order]
    norms = np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    similarity = np.clip((vectors / norms) @ (vectors / norms).T, -1.0, 1.0)
    distance = 1.0 - similarity
    np.fill_diagonal(distance, 0.0)
    return {
        "playlist_id": playlist_id,
        "indexes": indexes,
        "video_ids": video_ids,
        "distance": np.round(distance, 6).tolist(),
    }
