from urllib.parse import urlencode

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .playlists import (
    forget_playlist,
    is_public_youtube_playlist,
    list_embed_is_playable,
    playlist_id_from_url,
    public_playlist_videos,
)

INVALID_URL_MESSAGE = "The URL is invalid."
PAGE_SIZE = 7
# YouTube rejects an embed queue longer than this and reports the video
# unavailable, so a clicked song carries at most this many ids.
EMBED_QUEUE_LIMIT = 200


@csrf_exempt
def leave(request):
    playlist_url = request.POST.get("url") or request.GET.get("url", "")
    forget_playlist(playlist_id_from_url(playlist_url))
    if request.method == "POST":
        return HttpResponse(status=204)
    return redirect(reverse("landing"))


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
    use_list = list_embed_is_playable(playlist_id, first.id) if first else False
    page_videos = []
    for index, video in enumerate(videos):
        page_videos.append(
            {
                "id": video.id,
                "index": index,
                "title": video.title,
                "thumbnail_url": video.thumbnail_url,
                "embed_url": _player_url(
                    video.id, playlist_id, ordered_ids[index + 1 :], use_list
                ),
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
                _player_url(first.id, playlist_id, ordered_ids[1:], use_list)
                if first
                else ""
            ),
            "player_title": first.title if first else "",
            "playing_id": first.id if first else "",
            "leave_url": f"{reverse('leave')}?{urlencode({'url': playlist_url})}"
            if playlist_url
            else reverse("leave"),
        },
    )


def _player_url(video_id, playlist_id, following_ids, use_list):
    # A playlist whose list= embed plays uses that id so the whole list stays
    # in order. Mixes and playlists YouTube will not embed that way use the
    # loaded songs instead. The path video has to be first in that queue, or
    # YouTube starts on the next id and each link plays the song below it.
    params = {"autoplay": "1", "rel": "0", "enablejsapi": "1"}
    if use_list:
        params["list"] = playlist_id
    else:
        params["playlist"] = ",".join([video_id, *following_ids][:EMBED_QUEUE_LIMIT])
    return f"https://www.youtube.com/embed/{video_id}?{urlencode(params, safe=',')}"


def _page_number(raw_page, page_count):
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        return 1
    return min(max(page, 1), page_count)
