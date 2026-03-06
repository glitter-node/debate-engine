from django.urls import path

from api import views

urlpatterns = [
    path("", views.root),
    path("healthz", views.healthz),
    path("theses", views.theses),
    path("favicon.ico", views.favicon),
    path("robots.txt", views.robots_txt),
    path("sitemap.xml", views.sitemap_xml),
]
