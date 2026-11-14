import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse
from xml.etree import ElementTree

_PLAYLIST_ID = re.compile(r"^[A-Za-z0-9_-]{10,128}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_FEED_URL = "https://www.youtube.com/feeds/videos.xml"
_PLAYLIST_URL = "https://www.youtube.com/playlist"
_ATOM = "http://www.w3.org/2005/Atom"
_YT = "http://www.youtube.com/xml/schemas/2015"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_CACHE_TTL = 300
_cache = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class PlaylistVideo:
    id: str
    title: str
    thumbnail_url: str


def playlist_id_from_url(raw_url):
    return _playlist_id(raw_url) or ""


def is_public_youtube_playlist(raw_url):
    """Return True when raw_url points at a playlist YouTube publishes publicly."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return False
    return _feed_is_public(playlist_id)


def public_playlist_videos(raw_url):
    """Return the playlist's videos in playlist order, or an empty tuple."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return ()
    cached = _cached_videos(playlist_id)
    if cached is not None:
        return cached
    # Radio and Mix pages are not a normal playlist. The queue the user sees
    # is on the watch page; the playlist page says this type is unviewable,
    # and its feed is a different set of songs.
    if playlist_id.startswith("RD"):
        videos = _videos_from_watch(playlist_id, _watch_video_id(raw_url))
    else:
        videos = _videos_from_playlist_page(playlist_id)
        if videos is None:
            videos = _videos_from_feed(playlist_id)
    if videos is None:
        return ()
    _store_videos(playlist_id, videos)
    return videos


def _playlist_id(raw_url):
    parsed = _parsed_youtube_url(raw_url)
    if parsed is None:
        return None
    playlist_ids = parse_qs(parsed.query).get("list") or []
    if len(playlist_ids) != 1 or not _PLAYLIST_ID.fullmatch(playlist_ids[0]):
        return None
    return playlist_ids[0]


def _watch_video_id(raw_url):
    parsed = _parsed_youtube_url(raw_url)
    if parsed is None:
        return ""
    video_ids = parse_qs(parsed.query).get("v") or []
    if len(video_ids) == 1 and _VIDEO_ID.fullmatch(video_ids[0]):
        return video_ids[0]
    return ""


def _parsed_youtube_url(raw_url):
    text = (raw_url or "").strip()
    if not text or len(text) > 2048:
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not _is_youtube_host(parsed.hostname):
        return None
    return parsed


def _is_youtube_host(hostname):
    host = (hostname or "").lower().rstrip(".")
    names = ("youtu.be", "youtube.com", "youtube-nocookie.com")
    return any(host == name or host.endswith("." + name) for name in names)


def _feed_is_public(playlist_id):
    # YouTube only serves this Atom feed for public playlists. Missing, private,
    # and unlisted playlists respond with 404.
    body = _fetch_bytes(_feed_request(playlist_id), limit=256)
    if body is None:
        return False
    opening = body.lstrip()
    return opening.startswith(b"<?xml") or opening.startswith(b"<feed")


def _videos_from_playlist_page(playlist_id):
    html = _fetch_text(_playlist_request(playlist_id))
    if html is None:
        return None
    data = _initial_data(html)
    if data is None:
        return None
    return _videos_from_initial_data(data)


def _videos_from_watch(playlist_id, video_id):
    query = {"list": playlist_id}
    if video_id:
        query["v"] = video_id
    if playlist_id.startswith("RD"):
        query["start_radio"] = "1"
    request = urllib.request.Request(
        "https://www.youtube.com/watch?" + urlencode(query),
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    html = _fetch_text(request)
    if html is None:
        return None
    data = _initial_data(html)
    if data is None:
        return None
    try:
        contents = data["contents"]["twoColumnWatchNextResults"]["playlist"]["playlist"][
            "contents"
        ]
    except (KeyError, TypeError):
        return None
    videos = []
    seen = set()
    for item in contents:
        if not isinstance(item, dict):
            continue
        renderer = item.get("playlistPanelVideoRenderer")
        if not isinstance(renderer, dict):
            continue
        video = _video(
            renderer.get("videoId") or "",
            _title_text(renderer.get("title")),
            seen,
        )
        if video is not None:
            videos.append(video)
    return tuple(videos)


def _title_text(title):
    if isinstance(title, dict):
        runs = title.get("runs") or []
        if runs:
            return "".join(part.get("text", "") for part in runs).strip()
        return (title.get("simpleText") or title.get("content") or "").strip()
    return str(title or "").strip()


def _videos_from_feed(playlist_id):
    body = _fetch_bytes(_feed_request(playlist_id), limit=2_000_000)
    if body is None:
        return None
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None
    videos = []
    seen = set()
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        video_id = (entry.findtext(f"{{{_YT}}}videoId") or "").strip()
        title = (entry.findtext(f"{{{_ATOM}}}title") or "").strip()
        video = _video(video_id, title, seen)
        if video is not None:
            videos.append(video)
    return tuple(videos)


def _videos_from_initial_data(data):
    try:
        sections = data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0][
            "tabRenderer"
        ]["content"]["sectionListRenderer"]["contents"]
    except (KeyError, IndexError, TypeError):
        return None
    videos = []
    seen = set()
    for section in sections:
        items = section.get("itemSectionRenderer", {}).get("contents", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            lock = item.get("lockupViewModel")
            if not isinstance(lock, dict):
                continue
            if lock.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
                continue
            video = _video(lock.get("contentId") or "", _lockup_title(lock), seen)
            if video is not None:
                videos.append(video)
    return tuple(videos)


def _lockup_title(lock):
    title = lock.get("metadata", {}).get("lockupMetadataViewModel", {}).get("title", {})
    if isinstance(title, dict):
        return (title.get("content") or "").strip()
    return str(title or "").strip()


def _video(video_id, title, seen):
    video_id = str(video_id).strip()
    title = title.strip()
    if not title or not _VIDEO_ID.fullmatch(video_id) or video_id in seen:
        return None
    seen.add(video_id)
    return PlaylistVideo(
        id=video_id,
        title=title,
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    )


def _initial_data(html):
    marker = html.find("ytInitialData")
    if marker < 0:
        return None
    start = html.find("{", marker)
    if start < 0:
        return None
    end = _json_end(html, start)
    if end is None:
        return None
    try:
        return json.loads(html[start:end])
    except json.JSONDecodeError:
        return None


def _json_end(text, start):
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _feed_request(playlist_id):
    return urllib.request.Request(
        _FEED_URL + "?" + urlencode({"playlist_id": playlist_id}),
        headers={"User-Agent": _USER_AGENT},
    )


def _playlist_request(playlist_id):
    return urllib.request.Request(
        _PLAYLIST_URL + "?" + urlencode({"list": playlist_id}),
        headers={
            "User-Agent": _USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _fetch_text(request):
    body = _fetch_bytes(request, limit=3_000_000)
    if body is None:
        return None
    return body.decode("utf-8", "replace")


def _fetch_bytes(request, limit):
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower() and "xml" not in content_type.lower():
                return None
            return response.read(limit)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _cached_videos(playlist_id):
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(playlist_id)
        if cached is None or now - cached[0] >= _CACHE_TTL:
            return None
        return cached[1]


def _store_videos(playlist_id, videos):
    with _cache_lock:
        _cache[playlist_id] = (time.monotonic(), videos)
