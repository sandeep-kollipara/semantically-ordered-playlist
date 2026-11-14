from urllib.parse import urlencode

from django.shortcuts import redirect, render
from django.urls import reverse

from .playlists import (
    is_public_youtube_playlist,
    playlist_id_from_url,
    public_playlist_videos,
)

INVALID_URL_MESSAGE = "The URL is invalid."
PAGE_SIZE = 7


def landing(request):
    submitted_url = ""
    error = ""
    if request.method == "POST":
        submitted_url = request.POST.get("url", "").strip()
        if is_public_youtube_playlist(submitted_url):
            return redirect(f"{reverse('liftoff')}?{urlencode({'url': submitted_url})}")
        error = INVALID_URL_MESSAGE
    return render(
        request,
        "pages/landing.html",
        {"error": error, "submitted_url": submitted_url},
    )


def liftoff(request):
    playlist_url = request.GET.get("url", "").strip()
    videos = public_playlist_videos(playlist_url)
    playlist_id = playlist_id_from_url(playlist_url)
    page_count = max(1, (len(videos) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = _page_number(request.GET.get("page"), page_count)
    start = (page - 1) * PAGE_SIZE
    ordered_ids = [video.id for video in videos]
    first = videos[0] if videos else None
    page_videos = []
    for index, video in enumerate(videos):
        page_videos.append(
            {
                "id": video.id,
                "index": index,
                "title": video.title,
                "thumbnail_url": video.thumbnail_url,
                "embed_url": _player_url(video.id, playlist_id, ordered_ids[index + 1 :]),
            }
        )
    return render(
        request,
        "pages/liftoff.html",
        {
            "videos": page_videos,
            "page": page,
            "page_size": PAGE_SIZE,
            "page_start": start,
            "page_end": start + PAGE_SIZE,
            "has_previous": page > 1,
            "has_next": page < page_count,
            "embed_url": (
                _player_url(first.id, playlist_id, ordered_ids[1:]) if first else ""
            ),
            "player_title": first.title if first else "",
            "playing_id": first.id if first else "",
        },
    )


def _player_url(video_id, playlist_id, following_ids):
    # A public playlist id keeps playback in playlist order. YouTube Mix and
    # radio ids (RD...) cannot be embedded, and attaching one makes the player
    # say the video is unavailable, so those use the loaded song order instead.
    params = {"autoplay": "1", "rel": "0", "enablejsapi": "1"}
    if playlist_id and not playlist_id.startswith("RD"):
        params["list"] = playlist_id
    else:
        # The path video has to be first in this queue. Otherwise YouTube
        # starts on the next id, so each link plays the song below it.
        params["playlist"] = ",".join([video_id, *following_ids])
    return f"https://www.youtube.com/embed/{video_id}?{urlencode(params, safe=',')}"


def _page_number(raw_page, page_count):
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        return 1
    return min(max(page, 1), page_count)
