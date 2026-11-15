import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse

from .playlists import PlaylistVideo

_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9]{22}$")
_TRACK_URI = re.compile(r"^spotify:track:([A-Za-z0-9]{22})$")
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
_EMBED_URL = "https://open.spotify.com/embed/playlist/{playlist_id}"
_OEMBED_URL = "https://open.spotify.com/oembed"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CACHE_TTL = 300
_entity_cache = {}
_video_cache = {}
_cache_lock = threading.Lock()


def spotify_playlist_id(raw_url):
    return _playlist_id(raw_url) or ""


def spotify_playlist_name(raw_url):
    """Return the playlist title, or an empty string when it cannot be read."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return ""
    entity = _entity(playlist_id)
    if not entity:
        return ""
    return " ".join(
        str(entity.get("name") or entity.get("title") or "").replace("\xa0", " ").split()
    )


def is_public_spotify_playlist(raw_url):
    """Return True when raw_url is a Spotify playlist the embed page publishes."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return False
    return _entity(playlist_id) is not None


def public_spotify_tracks(raw_url):
    """Return the playlist's tracks in playlist order, or an empty tuple."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return ()
    cached = _cached_videos(playlist_id)
    if cached is not None:
        return cached
    entity = _entity(playlist_id)
    if entity is None:
        return ()
    videos = _videos_from_entity(entity)
    _store_videos(playlist_id, videos)
    return videos


def forget_spotify_playlist(playlist_id):
    if not playlist_id:
        return
    with _cache_lock:
        _entity_cache.pop(playlist_id, None)
        _video_cache.pop(playlist_id, None)


def spotify_embed_url(playlist_id, track_id=""):
    base = _EMBED_URL.format(playlist_id=playlist_id)
    if not track_id:
        return base
    return f"{base}?{urlencode({'highlight': f'spotify:track:{track_id}'})}"


def _playlist_id(raw_url):
    text = (raw_url or "").strip()
    if not text or len(text) > 2048:
        return None
    if text.lower().startswith("spotify:playlist:"):
        playlist_id = text.split(":", 2)[2].split("?")[0]
        return playlist_id if _PLAYLIST_ID.fullmatch(playlist_id) else None
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not _is_spotify_host(parsed.hostname):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    try:
        index = parts.index("playlist")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    playlist_id = parts[index + 1]
    return playlist_id if _PLAYLIST_ID.fullmatch(playlist_id) else None


def _is_spotify_host(hostname):
    host = (hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host == "spotify.com" or host.endswith(".spotify.com")


def _entity(playlist_id):
    cached = _cached_entity(playlist_id)
    if cached is not None:
        return cached
    html = _fetch_text(_EMBED_URL.format(playlist_id=playlist_id))
    entity = _entity_from_html(html)
    if entity is None:
        return None
    _store_entity(playlist_id, entity)
    return entity


def _entity_from_html(html):
    match = _NEXT_DATA.search(html or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    data = payload.get("props", {}).get("pageProps", {}).get("state", {}).get("data")
    entity = data.get("entity") if isinstance(data, dict) else None
    if not isinstance(entity, dict) or entity.get("type") != "playlist":
        return None
    if not entity.get("name") and not entity.get("title"):
        return None
    return entity


def _videos_from_entity(entity):
    cover = _cover_url(entity)
    songs = []
    for track in entity.get("trackList") or []:
        if not isinstance(track, dict) or track.get("isPlayable") is False:
            continue
        match = _TRACK_URI.fullmatch(track.get("uri") or "")
        if not match:
            continue
        title = _display_title(track.get("title") or "", track.get("subtitle") or "")
        if title:
            songs.append((match.group(1), title))
    thumbs = _thumbnails([track_id for track_id, _title in songs], cover)
    return tuple(
        PlaylistVideo(track_id, title, thumb or cover)
        for (track_id, title), thumb in zip(songs, thumbs)
    )


def _cover_url(entity):
    sources = (entity.get("coverArt") or {}).get("sources") or []
    urls = [source.get("url") for source in sources if isinstance(source, dict) and source.get("url")]
    return urls[-1] if urls else ""


def _display_title(title, subtitle):
    name = " ".join(str(title).replace("\xa0", " ").split())
    artists = " ".join(str(subtitle).replace("\xa0", " ").split())
    if artists and artists.lower() not in name.lower():
        return f"{name} — {artists}"
    return name


def _thumbnails(track_ids, fallback):
    if not track_ids:
        return []

    def one(track_id):
        url = f"{_OEMBED_URL}?{urlencode({'url': f'https://open.spotify.com/track/{track_id}'})}"
        payload = _fetch_json(url)
        thumb = payload.get("thumbnail_url") if isinstance(payload, dict) else ""
        return thumb or fallback

    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, track_ids))


def _fetch_text(url):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type:
                return ""
            return response.read(2_000_000).decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def _fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type:
                return None
            return json.loads(response.read(1_000_000).decode("utf-8", "replace"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _cached_entity(playlist_id):
    return _cached(_entity_cache, playlist_id)


def _cached_videos(playlist_id):
    return _cached(_video_cache, playlist_id)


def _cached(cache, playlist_id):
    with _cache_lock:
        cached = cache.get(playlist_id)
        if cached and time.monotonic() - cached[0] < _CACHE_TTL:
            return cached[1]
    return None


def _store_entity(playlist_id, entity):
    _store(_entity_cache, playlist_id, entity)


def _store_videos(playlist_id, videos):
    _store(_video_cache, playlist_id, videos)


def _store(cache, playlist_id, value):
    with _cache_lock:
        cache[playlist_id] = (time.monotonic(), value)
