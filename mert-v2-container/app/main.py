"""HTTP API for MERT-v2-30s embeddings and nearest-centroid classification."""

import json
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, HTTPException, Request

from app.audio import AudioError, load_clip
from app.encoder import encoder
from app.store import (
    add_example,
    centroids,
    cosine,
    delete_class,
    layer_of_store,
    list_classes,
)

_LAYER_HELP = (
    "Optional transformer block from 1 to 24. "
    "Omit it to use the final hidden state. "
    "The model card suggests block 23 for genre and block 22 for tagging."
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    encoder.load()
    yield


app = FastAPI(
    title="MERT-v2-30s",
    summary="Embeddings and classification for 30-second music clips.",
    lifespan=lifespan,
)


def _layer(request: Request) -> int | None:
    raw = request.query_params.get("layer")
    if raw is None or raw == "":
        return None
    try:
        layer = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Layer must be an integer.") from exc
    if encoder.ready and not 1 <= layer <= encoder.layers:
        raise HTTPException(
            status_code=400,
            detail=f"Layer must be from 1 to {encoder.layers}.",
        )
    return layer


def _vector_payload(vector: np.ndarray, info: dict, layer: int | None) -> dict:
    return {
        "model": encoder.model_id,
        "layer": layer,
        "dimension": int(vector.shape[0]),
        "sample_rate": info["sample_rate"],
        "duration_seconds": info["duration_seconds"],
        "samples": info["samples"],
        "padded": info["padded"],
        "truncated": info["truncated"],
        "embedding": vector.astype(float).tolist(),
    }


async def _clip_from_request(request: Request) -> tuple[bytes, str]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/"):
        form = await request.form()
        upload = form.get("audio")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing audio file field 'audio'.")
        data = await upload.read()
        filename = getattr(upload, "filename", None) or "audio.wav"
        return data, filename
    if content_type.startswith("audio/") or content_type == "application/octet-stream":
        return await request.body(), "audio.wav"
    raise HTTPException(
        status_code=415,
        detail="Send a multipart field named audio, or a raw audio body.",
    )


def _prepare(data: bytes, filename: str) -> tuple[np.ndarray, dict]:
    try:
        return load_clip(data, filename, encoder.sample_rate)
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _embed(data: bytes, filename: str, layer: int | None) -> tuple[np.ndarray, dict]:
    waveform, info = _prepare(data, filename)
    try:
        vector = encoder.embed(waveform, layer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return vector, info


@app.get("/health")
def health():
    return {
        "ready": encoder.ready,
        "model": encoder.model_id,
        "device": encoder.device,
        "dimension": encoder.dimension,
        "layers": encoder.layers,
        "clip_seconds": 30,
        "sample_rate": encoder.sample_rate,
    }


@app.get("/")
def index():
    return {
        "model": encoder.model_id,
        "clip_seconds": 30,
        "endpoints": {
            "GET /health": "Model load status.",
            "POST /embeddings": f"1024-d embedding of a 30-second clip. {_LAYER_HELP}",
            "POST /classes": "Register an example clip under a class label.",
            "GET /classes": "List registered class labels.",
            "DELETE /classes/{label}": "Remove one class.",
            "POST /classify": "Nearest registered class for a 30-second clip, plus its embedding.",
        },
    }


@app.post("/embeddings")
async def embeddings(request: Request):
    data, filename = await _clip_from_request(request)
    layer = _layer(request)
    vector, info = _embed(data, filename, layer)
    return _vector_payload(vector, info, layer)


@app.get("/classes")
def classes():
    return {"layer": layer_of_store(), "classes": list_classes()}


@app.post("/classes")
async def register_class(request: Request):
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/"):
        raise HTTPException(status_code=415, detail="Send multipart fields 'label' and 'audio'.")
    form = await request.form()
    label = form.get("label")
    upload = form.get("audio")
    if not isinstance(label, str) or not label.strip():
        raise HTTPException(status_code=400, detail="Missing class label.")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Missing audio file field 'audio'.")
    layer = _layer(request)
    vector, _info = _embed(await upload.read(), getattr(upload, "filename", None) or "audio.wav", layer)
    try:
        saved = add_example(label, vector, layer)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return saved


@app.delete("/classes/{label}")
def remove_class(label: str):
    if not delete_class(label):
        raise HTTPException(status_code=404, detail="Class not found.")
    return {"deleted": label}


@app.post("/classify")
async def classify(request: Request):
    content_type = request.headers.get("content-type", "")
    supplied = None
    if content_type.startswith("multipart/"):
        form = await request.form()
        upload = form.get("audio")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Missing audio file field 'audio'.")
        data = await upload.read()
        filename = getattr(upload, "filename", None) or "audio.wav"
        raw_prototypes = form.get("prototypes")
        if isinstance(raw_prototypes, str) and raw_prototypes.strip():
            try:
                supplied = json.loads(raw_prototypes)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=400, detail="prototypes must be JSON.") from exc
    else:
        data, filename = await _clip_from_request(request)

    layer = _layer(request)
    vector, info = _embed(data, filename, layer)
    try:
        pairs = _prototype_pairs(supplied, vector.shape[0], layer)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not pairs:
        raise HTTPException(
            status_code=409,
            detail="No classes are registered. POST an example clip to /classes, "
            "or send a prototypes JSON object with this request.",
        )
    scores = [
        {"label": label, "score": round(cosine(vector, centroid), 6)}
        for label, centroid in pairs
    ]
    scores.sort(key=lambda item: item["score"], reverse=True)
    payload = _vector_payload(vector, info, layer)
    payload["label"] = scores[0]["label"]
    payload["score"] = scores[0]["score"]
    payload["scores"] = scores
    return payload


def _prototype_pairs(supplied, dimension: int, layer: int | None):
    if supplied is None:
        try:
            return centroids(layer)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not isinstance(supplied, dict) or not supplied:
        raise ValueError("prototypes must be an object of label to embedding.")
    pairs = []
    for label, values in supplied.items():
        if not isinstance(label, str) or not isinstance(values, list):
            raise ValueError("Each prototype must be a label mapped to a number list.")
        centroid = np.asarray(values, dtype=np.float64)
        if centroid.shape != (dimension,) or not np.isfinite(centroid).all():
            raise ValueError(f"Prototype '{label}' must have {dimension} finite values.")
        pairs.append((label, centroid))
    return pairs
