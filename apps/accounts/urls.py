"""Account/auth URLs: registration and activation."""

from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register_choice, name="register"),
    path("register/landlord/", views.register_landlord, name="register_landlord"),
    path("register/tenant/", views.register_tenant, name="register_tenant"),
    path("resend-activation/", views.resend_activation, name="resend_activation"),
    path("activate/<uidb64>/<token>/", views.activate, name="activate"),
    path("post-login/", views.post_login, name="post_login"),
]
