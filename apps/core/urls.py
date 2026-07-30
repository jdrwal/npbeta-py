from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("flats/", views.flats, name="flats"),
    path("flats/add/", views.add_flat, name="add_flat"),
    path("flats/<int:pk>/edit/", views.FlatUpdate.as_view(), name="edit_flat"),
    path("flats/<int:pk>/delete/", views.FlatDelete.as_view(), name="delete_flat"),
    path("rooms/", views.rooms, name="rooms"),
    path("rooms/add/", views.RoomCreate.as_view(), name="add_room"),
    path("rooms/<int:pk>/edit/", views.RoomUpdate.as_view(), name="edit_room"),
    path("rooms/<int:pk>/delete/", views.RoomDelete.as_view(), name="delete_room"),
    path("contracts/", views.contracts, name="contracts"),
    path("contracts/add/", views.ContractCreate.as_view(), name="add_contract"),
    path("contracts/<int:pk>/edit/", views.ContractUpdate.as_view(), name="edit_contract"),
    path(
        "contracts/<int:pk>/delete/",
        views.ContractDelete.as_view(),
        name="delete_contract",
    ),
    path("records/", views.records, name="records"),
    path("records/add/", views.RecordCreate.as_view(), name="add_record"),
    path("records/<int:pk>/edit/", views.RecordUpdate.as_view(), name="edit_record"),
    path("records/<int:pk>/delete/", views.RecordDelete.as_view(), name="delete_record"),
    path("calculations/", views.calculations, name="calculations"),
    path("calculations/new/", views.run_settlement, name="run_settlement"),
    path("calculations/<int:pk>/", views.calculation_detail, name="calculation_detail"),
    path(
        "calculations/<int:pk>/delete/",
        views.delete_settlement,
        name="delete_settlement",
    ),
    path(
        "calculations/<int:pk>/email/",
        views.email_settlement,
        name="email_settlement",
    ),
    path("readings/add/", views.add_reading, name="add_reading"),
    path("readings/<int:pk>/edit/", views.ReadingUpdate.as_view(), name="edit_reading"),
    path(
        "readings/<int:pk>/delete/",
        views.ReadingDelete.as_view(),
        name="delete_reading",
    ),
    path("counters/", views.counters, name="counters"),
    path("counters/add/", views.MeterCreate.as_view(), name="add_meter"),
    path("counters/<int:pk>/edit/", views.MeterUpdate.as_view(), name="edit_meter"),
    path("counters/<int:pk>/delete/", views.MeterDelete.as_view(), name="delete_meter"),
    path("counters/<int:pk>/readings/", views.meter_readings, name="meter_readings"),
    path("tax/", views.tax, name="tax"),
    path("forecast/", views.forecast, name="forecast"),
    path("healthz/", views.healthz, name="healthz"),
]
