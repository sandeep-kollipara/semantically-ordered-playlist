"""Decode an uploaded clip and fit it to MERT-v2-30s's 30-second input."""

import io
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
import torchaudio.functional as AF

CLIP_SECONDS = 30.0
TOLERANCE_SECONDS = 0.5
MAX_AUDIO_BYTES = 40 * 1024 * 1024


class AudioError(ValueError):
    """The upload is not a usable 30-second clip."""


def load_clip(data: bytes, filename: str, sample_rate: int) -> tuple[np.ndarray, dict]:
    if not data:
        raise AudioError("Audio file is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise AudioError("Audio file is larger than 40 MB.")

    waveform, source_rate = _decode(data, filename)
    if waveform.size == 0 or source_rate <= 0:
        raise AudioError("Audio file has no samples.")
    if not np.isfinite(waveform).all():
        raise AudioError("Audio file contains non-finite samples.")

    duration = float(waveform.size) / float(source_rate)
    if abs(duration - CLIP_SECONDS) > TOLERANCE_SECONDS:
        raise AudioError(
            f"Audio must be a {CLIP_SECONDS:.0f}-second clip "
            f"(within {TOLERANCE_SECONDS:.1f}s). Received {duration:.2f} seconds."
        )

    samples = torch.from_numpy(np.ascontiguousarray(waveform, dtype=np.float32))
    if source_rate != sample_rate:
        samples = AF.resample(samples, source_rate, sample_rate)

    target = int(round(CLIP_SECONDS * sample_rate))
    padded = samples.numel() < target
    truncated = samples.numel() > target
    if padded:
        samples = torch.nn.functional.pad(samples, (0, target - samples.numel()))
    elif truncated:
        samples = samples[:target]

    return samples.numpy(), {
        "duration_seconds": round(duration, 3),
        "sample_rate": sample_rate,
        "samples": int(samples.numel()),
        "padded": padded,
        "truncated": truncated,
    }


def _decode(data: bytes, filename: str) -> tuple[np.ndarray, int]:
    try:
        audio, source_rate = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    except Exception:
        audio, source_rate = _decode_with_ffmpeg(data, filename)
    else:
        audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim == 1:
        mono = audio
    else:
        mono = audio.mean(axis=1)
    return np.ascontiguousarray(mono, dtype=np.float32), int(source_rate)


def _decode_with_ffmpeg(data: bytes, filename: str) -> tuple[np.ndarray, int]:
    suffix = Path(filename).suffix.lower()
    if len(suffix) > 8 or not suffix.replace(".", "").isalnum():
        suffix = ".bin"
    source_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as source:
            source.write(data)
            source_path = source.name
        wav_path = f"{source_path}.wav"
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-i",
                source_path,
                "-ac",
                "1",
                "-f",
                "wav",
                wav_path,
            ],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AudioError("Audio file could not be decoded.")
        audio, source_rate = sf.read(wav_path, dtype="float32", always_2d=False)
        return np.asarray(audio, dtype=np.float32), int(source_rate)
    finally:
        for path in (source_path, wav_path):
            if path and os.path.exists(path):
                os.unlink(path)
