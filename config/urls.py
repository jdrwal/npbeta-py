"""URL configuration for the npbeta project."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("apps.accounts.urls")),
    path(
        "prywatnosc/",
        TemplateView.as_view(template_name="privacy.html"),
        name="privacy",
    ),
    path("", include("apps.core.urls")),
]
