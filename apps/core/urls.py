from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("flats/", views.flats, name="flats"),
    path("contracts/", views.contracts, name="contracts"),
    path("records/", views.records, name="records"),
    path("calculations/", views.calculations, name="calculations"),
    path("calculations/new/", views.run_settlement, name="run_settlement"),
    path("calculations/<int:pk>/", views.calculation_detail, name="calculation_detail"),
    path(
        "calculations/<int:pk>/delete/",
        views.delete_settlement,
        name="delete_settlement",
    ),
    path("flats/add/", views.add_flat, name="add_flat"),
    path("readings/add/", views.add_reading, name="add_reading"),
    path("healthz/", views.healthz, name="healthz"),
]
