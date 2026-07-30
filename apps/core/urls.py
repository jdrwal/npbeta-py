from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("flats/", views.flats, name="flats"),
    path("contracts/", views.contracts, name="contracts"),
    path("records/", views.records, name="records"),
    path("calculations/", views.calculations, name="calculations"),
    path("calculations/<int:pk>/", views.calculation_detail, name="calculation_detail"),
    path("healthz/", views.healthz, name="healthz"),
]
