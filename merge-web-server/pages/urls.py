from django.urls import path

from . import views

urlpatterns = [
    path("manifest.webmanifest", views.web_manifest, name="manifest"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("", views.landing, name="landing"),
    path("leave/", views.leave, name="leave"),
    path("vectorize/", views.vectorize, name="vectorize"),
    path("vectorize/<str:job_id>/", views.vectorize_job, name="vectorize_job"),
    path("liftoff/", views.liftoff, name="liftoff"),
    path("liftoff2/", views.liftoff2, name="liftoff2"),
]
