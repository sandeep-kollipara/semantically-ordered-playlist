import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import unescape
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
_MAX_PLAYLIST_BATCHES = 50
_INNERTUBE_KEY = re.compile(r'"INNERTUBE_API_KEY":"([^"]+)"')
_INNERTUBE_VERSION = re.compile(r'"INNERTUBE_CLIENT_VERSION":"([^"]+)"')
_PLAYLIST_TITLE = re.compile(
    r'"playlistMetadataRenderer":\{"title":"((?:\\.|[^"\\])*)"'
)
_PAGE_TITLE = re.compile(r'"pageTitle":"((?:\\.|[^"\\])*)"')
_DOCUMENT_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_cache = {}
_embed_mode_cache = {}
_title_cache = {}
_cache_lock = threading.Lock()


@dataclass(frozen=True)
class PlaylistVideo:
    id: str
    title: str
    thumbnail_url: str


def playlist_id_from_url(raw_url):
    return _playlist_id(raw_url) or ""


def youtube_playlist_name(raw_url):
    """Return the playlist title, or an empty string when it cannot be read."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return ""
    cached = _cached_title(playlist_id)
    if cached:
        return cached
    html = _fetch_text(_playlist_request(playlist_id), limit=1_500_000)
    name = _clean_playlist_name(_title_from_html(html or ""))
    if name:
        _store_title(playlist_id, name)
    return name


def is_public_youtube_playlist(raw_url):
    """Return True when raw_url points at a playlist YouTube publishes publicly."""
    playlist_id = _playlist_id(raw_url)
    if playlist_id is None:
        return False
    if _feed_is_public(playlist_id):
        return True
    # Some public playlists 404 on the Atom feed while the playlist page still
    # publishes them. That page records "unlisted": false for a public list.
    if playlist_id.startswith("RD"):
        return False
    return _playlist_page_is_public(playlist_id)


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


def _playlist_page_is_public(playlist_id):
    html = _fetch_text(_playlist_request(playlist_id), limit=8_000_000)
    if not html or f"list={playlist_id}" not in html:
        return False
    return '"unlisted":false' in html


def _feed_is_public(playlist_id):
    # YouTube only serves this Atom feed for public playlists. Missing, private,
    # and unlisted playlists respond with 404.
    body = _fetch_bytes(_feed_request(playlist_id), limit=256)
    if body is None:
        return False
    opening = body.lstrip()
    return opening.startswith(b"<?xml") or opening.startswith(b"<feed")


def _videos_from_playlist_page(playlist_id):
    html = _fetch_text(_playlist_request(playlist_id), limit=8_000_000)
    if html is None:
        return None
    _remember_title(playlist_id, _title_from_html(html))
    data = _initial_data(html)
    if data is None:
        return None
    items = _playlist_items(data)
    if items is None:
        return None
    videos = []
    # The playlist page includes the first batch only. The last row is a
    # continuation token, which is what the page sends when it is scrolled.
    _collect_playlist_items(items, videos, set(), _innertube_client(html))
    return tuple(videos)


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
        playlist = data["contents"]["twoColumnWatchNextResults"]["playlist"]["playlist"]
        contents = playlist["contents"]
    except (KeyError, TypeError):
        return None
    _remember_title(
        playlist_id,
        _title_text(playlist.get("title")) or _title_from_html(html),
    )
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
    _remember_title(playlist_id, root.findtext(f"{{{_ATOM}}}title") or "")
    videos = []
    seen = set()
    for entry in root.findall(f"{{{_ATOM}}}entry"):
        video_id = (entry.findtext(f"{{{_YT}}}videoId") or "").strip()
        title = (entry.findtext(f"{{{_ATOM}}}title") or "").strip()
        video = _video(video_id, title, seen)
        if video is not None:
            videos.append(video)
    return tuple(videos)


def _playlist_items(data):
    try:
        sections = data["contents"]["twoColumnBrowseResultsRenderer"]["tabs"][0][
            "tabRenderer"
        ]["content"]["sectionListRenderer"]["contents"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(sections, list):
        return None
    for section in sections:
        if not isinstance(section, dict):
            continue
        renderer = section.get("itemSectionRenderer")
        items = renderer.get("contents") if isinstance(renderer, dict) else None
        if not isinstance(items, list):
            continue
        if any(isinstance(item, dict) and "lockupViewModel" in item for item in items):
            return items
    return []


def _collect_playlist_items(items, videos, seen, client):
    seen_tokens = set()
    batch = items
    for _ in range(_MAX_PLAYLIST_BATCHES):
        if not isinstance(batch, list) or not batch:
            return
        token = ""
        for item in batch:
            if not isinstance(item, dict):
                continue
            lock = item.get("lockupViewModel")
            if isinstance(lock, dict):
                _append_lockup(lock, videos, seen)
                continue
            next_token = _continuation_token(item)
            if next_token:
                token = next_token
        if not token or token in seen_tokens or client is None:
            return
        seen_tokens.add(token)
        batch = _browse_continuation(token, client)


def _append_lockup(lock, videos, seen):
    if lock.get("contentType") not in (None, "LOCKUP_CONTENT_TYPE_VIDEO"):
        return
    video = _video(lock.get("contentId") or "", _lockup_title(lock), seen)
    if video is not None:
        videos.append(video)


def _continuation_token(item):
    view = item.get("continuationItemViewModel")
    if not isinstance(view, dict):
        return ""
    command = view.get("continuationCommand")
    if not isinstance(command, dict):
        return ""
    inner = command.get("innertubeCommand")
    if isinstance(inner, dict):
        metadata = inner.get("commandMetadata")
        web = metadata.get("webCommandMetadata") if isinstance(metadata, dict) else None
        api_url = web.get("apiUrl") if isinstance(web, dict) else ""
        if api_url and api_url != "/youtubei/v1/browse":
            return ""
        nested = inner.get("continuationCommand")
        token = nested.get("token") if isinstance(nested, dict) else ""
        if isinstance(token, str) and token:
            return token
    token = command.get("token")
    return token if isinstance(token, str) else ""


def _innertube_client(html):
    key = _INNERTUBE_KEY.search(html or "")
    version = _INNERTUBE_VERSION.search(html or "")
    if key is None or version is None:
        return None
    return key.group(1), version.group(1)


def _browse_continuation(token, client):
    key, version = client
    body = json.dumps(
        {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": version,
                    "hl": "en",
                    "gl": "US",
                }
            },
            "continuation": token,
        }
    ).encode()
    request = urllib.request.Request(
        "https://www.youtube.com/youtubei/v1/browse?"
        + urlencode({"key": key, "prettyPrint": "false"}),
        data=body,
        headers={
            "User-Agent": _USER_AGENT,
            "Content-Type": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": "https://www.youtube.com",
            "X-Youtube-Client-Name": "1",
            "X-Youtube-Client-Version": version,
        },
    )
    payload = _fetch_json(request)
    if not isinstance(payload, dict):
        return None
    actions = payload.get("onResponseReceivedActions")
    if not isinstance(actions, list):
        return None
    renderers = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        append = action.get("appendContinuationItemsAction")
        if not isinstance(append, dict):
            continue
        continuation_items = append.get("continuationItems")
        if isinstance(continuation_items, list):
            renderers.extend(_flatten_renderers(continuation_items))
    return renderers


def _flatten_renderers(items):
    flat = []
    for item in items:
        if not isinstance(item, dict):
            continue
        section = item.get("itemSectionRenderer")
        nested = section.get("contents") if isinstance(section, dict) else None
        if isinstance(nested, list):
            flat.extend(entry for entry in nested if isinstance(entry, dict))
        else:
            flat.append(item)
    return flat


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


def _fetch_text(request, limit=3_000_000):
    body = _fetch_bytes(request, limit=limit)
    if body is None:
        return None
    return body.decode("utf-8", "replace")


def _fetch_json(request):
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "json" not in content_type.lower():
                return None
            return json.loads(response.read(8_000_000))
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return None


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


def list_embed_is_playable(playlist_id, video_id):
    """Return whether YouTube can embed this playlist with a list= parameter.

    Some public playlists still contain an unplayable video. Attaching their
    id makes the embed say the video is unavailable, even for a song that
    plays on its own.
    """
    if not playlist_id or playlist_id.startswith("RD") or not video_id:
        return False
    cached = _cached_embed_mode(playlist_id)
    if cached is not None:
        return cached
    playable = _embed_preview_ok(
        "https://www.youtube.com/embed/"
        + video_id
        + "?"
        + urlencode({"list": playlist_id})
    )
    _store_embed_mode(playlist_id, playable)
    return playable


def forget_playlist(playlist_id):
    if not playlist_id:
        return
    with _cache_lock:
        _cache.pop(playlist_id, None)
        _embed_mode_cache.pop(playlist_id, None)
        _title_cache.pop(playlist_id, None)


def _embed_preview_ok(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": "http://localhost:8765/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200:
                return True
            body = response.read(400_000).decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, ValueError):
        return True
    marker = body.find("previewPlayabilityStatus")
    if marker < 0:
        return True
    window = body[marker:marker + 240]
    return 'status\\":\\"OK\\"' in window or '"status":"OK"' in window


def _cached_embed_mode(playlist_id):
    now = time.monotonic()
    with _cache_lock:
        cached = _embed_mode_cache.get(playlist_id)
        if cached is None or now - cached[0] >= _CACHE_TTL:
            return None
        return cached[1]


def _store_embed_mode(playlist_id, playable):
    with _cache_lock:
        _embed_mode_cache[playlist_id] = (time.monotonic(), playable)


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


def _cached_title(playlist_id):
    now = time.monotonic()
    with _cache_lock:
        cached = _title_cache.get(playlist_id)
        if cached is None or now - cached[0] >= _CACHE_TTL:
            return ""
        return cached[1]


def _store_title(playlist_id, name):
    with _cache_lock:
        _title_cache[playlist_id] = (time.monotonic(), name)


def _remember_title(playlist_id, name):
    name = _clean_playlist_name(name)
    if name:
        _store_title(playlist_id, name)


def _title_from_html(html):
    for pattern in (_PLAYLIST_TITLE, _PAGE_TITLE):
        match = pattern.search(html or "")
        if match:
            return _decode_json_string(match.group(1))
    match = _DOCUMENT_TITLE.search(html or "")
    return unescape(match.group(1)) if match else ""


def _decode_json_string(raw):
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw


def _clean_playlist_name(name):
    name = " ".join(unescape(str(name or "")).replace("\xa0", " ").split())
    if name.lower().endswith(" - youtube"):
        name = name[: -len(" - youtube")].strip()
    return name
