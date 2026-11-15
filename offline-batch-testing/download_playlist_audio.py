#!/usr/bin/env python3
"""Download the first 30 seconds of audio from every video in a YouTube playlist.

Clips are written to audio_clips_30s next to this file. Run it with the local
virtualenv so yt-dlp and the bundled ffmpeg are available:

    .venv/bin/python download_playlist_audio.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLpBJZsS73i1d6JH6ilhxFNKxzgHpitIFZ"
ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "audio_clips_30s"


def ffmpeg_dir() -> Path:
    bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    tools = ROOT / ".tools" / "bin"
    tools.mkdir(parents=True, exist_ok=True)
    link = tools / "ffmpeg"
    if not link.exists():
        link.symlink_to(bundled)
    return tools


def ytdlp_bin() -> str:
    candidate = ROOT / ".venv" / "bin" / "yt-dlp"
    if candidate.exists():
        return str(candidate)
    return "yt-dlp"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    command = [
        ytdlp_bin(),
        "--ignore-errors",
        "--impersonate", "chrome",
        "--cookies-from-browser", "firefox",
        *(["--js-runtimes", f"node:{shutil.which('node')}"] if shutil.which("node") else []),
        "--no-abort-on-error",
        "--no-overwrites",
        "--yes-playlist",
        "--download-sections", "*0:00-0:30",
        "--force-keyframes-at-cuts",
        "-x",
        "--audio-format", "wav",
        "--ffmpeg-location", str(ffmpeg_dir()),
        "-o", str(OUTPUT_DIR / "%(playlist_index)03d_%(id)s.%(ext)s"),
        PLAYLIST_URL,
    ]
    completed = subprocess.run(command)
    saved = sorted(OUTPUT_DIR.glob("*.wav"))
    print(f"Saved {len(saved)} clips in {OUTPUT_DIR}", flush=True)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
