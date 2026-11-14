from django.urls import path

from . import views

urlpatterns = [
    path("", views.landing, name="landing"),
    path("leave/", views.leave, name="leave"),
    path("liftoff/", views.liftoff, name="liftoff"),
]
