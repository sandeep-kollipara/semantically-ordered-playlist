from django.shortcuts import render

# Placeholder until playlist URLs are turned into a real player queue.
RICK_ASTLEY_EMBED_URL = "https://www.youtube.com/embed/dQw4w9WgXcQ"


def landing(request):
    return render(request, "pages/landing.html")


def liftoff(request):
    return render(
        request,
        "pages/liftoff.html",
        {"embed_url": RICK_ASTLEY_EMBED_URL},
    )
