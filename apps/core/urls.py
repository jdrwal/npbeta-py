from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("flats/", views.flats, name="flats"),
    path("healthz/", views.healthz, name="healthz"),
]
