import json
from urllib.parse import urlencode

from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from .vectorize import start_vectorize, vectorize_status

from .playlists import (
    forget_playlist,
    is_public_youtube_playlist,
    list_embed_is_playable,
    playlist_id_from_url,
    public_playlist_videos,
    youtube_playlist_name,
)
from .spotify import (
    forget_spotify_playlist,
    is_public_spotify_playlist,
    public_spotify_tracks,
    spotify_embed_url,
    spotify_playlist_id,
    spotify_playlist_name,
)

INVALID_URL_MESSAGE = "The URL is invalid."
PAGE_SIZE = 7
# YouTube rejects an embed queue longer than this and reports the video
# unavailable, so a clicked song carries at most this many ids.
EMBED_QUEUE_LIMIT = 200


@csrf_exempt
def vectorize(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required."}, status=405)
    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        payload = {}
    playlist_url = (payload.get("url") or request.POST.get("url") or "").strip()
    try:
        job_id = start_vectorize(playlist_url)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"job": job_id})


@csrf_exempt
def vectorize_job(request, job_id):
    status = vectorize_status(job_id)
    if status is None:
        return JsonResponse({"error": "Unknown vectorize job."}, status=404)
    return JsonResponse(status)


@csrf_exempt
def leave(request):
    playlist_url = request.POST.get("url") or request.GET.get("url", "")
    spotify_id = spotify_playlist_id(playlist_url)
    if spotify_id:
        forget_spotify_playlist(spotify_id)
    else:
        forget_playlist(playlist_id_from_url(playlist_url))
    if request.method == "POST":
        return HttpResponse(status=204)
    return redirect(reverse("landing"))


def landing(request):
    submitted_url = ""
    error = ""
    if request.method == "POST":
        submitted_url = request.POST.get("url", "").strip()
        if spotify_playlist_id(submitted_url):
            if is_public_spotify_playlist(submitted_url):
                return redirect(f"{reverse('liftoff2')}?{urlencode({'url': submitted_url})}")
        elif is_public_youtube_playlist(submitted_url):
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
            "leave_url": _leave_url(playlist_url),
            "playlist_service": "youtube",
            "playlist_id": playlist_id,
            "playlist_url": playlist_url,
            "playlist_name": youtube_playlist_name(playlist_url),
        },
    )


def liftoff2(request):
    playlist_url = request.GET.get("url", "").strip()
    playlist_id = spotify_playlist_id(playlist_url)
    videos = public_spotify_tracks(playlist_url) if playlist_id else ()
    page_count = max(1, (len(videos) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = _page_number(request.GET.get("page"), page_count)
    start = (page - 1) * PAGE_SIZE
    first = videos[0] if videos else None
    page_videos = [
        {
            "id": video.id,
            "index": index,
            "title": video.title,
            "thumbnail_url": video.thumbnail_url,
            "embed_url": spotify_embed_url(playlist_id, video.id),
        }
        for index, video in enumerate(videos)
    ]
    return render(
        request,
        "pages/liftoff2.html",
        {
            "videos": page_videos,
            "page": page,
            "page_size": PAGE_SIZE,
            "page_start": start,
            "page_end": start + PAGE_SIZE,
            "has_previous": page > 1,
            "has_next": page < page_count,
            "playlist_id": playlist_id,
            "embed_url": spotify_embed_url(playlist_id) if first else "",
            "player_title": first.title if first else "",
            "playing_id": first.id if first else "",
            "leave_url": _leave_url(playlist_url),
            "playlist_service": "spotify",
            "playlist_url": playlist_url,
            "playlist_name": spotify_playlist_name(playlist_url) if playlist_id else "",
        },
    )


def _leave_url(playlist_url):
    if not playlist_url:
        return reverse("leave")
    return f"{reverse('leave')}?{urlencode({'url': playlist_url})}"


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
