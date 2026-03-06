from api import views as api_views
from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("logout/", LogoutView.as_view(next_page="thinking:home"), name="logout"),
    path("auth/", include("authflow.urls")),
    path("health", api_views.healthz),
    path("health/", api_views.healthz),
    path("favicon.ico", api_views.favicon),
    path("robots.txt", api_views.robots_txt),
    path("sitemap.xml", api_views.sitemap_xml),
    path("api/", include("api.urls")),
    path("", include("thinking.urls")),
]
