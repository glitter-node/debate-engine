"""
app.authflow.urls
URL configuration for the authflow app.
"""
from django.urls import path

from . import views

app_name = "authflow"

urlpatterns = [
    path("", views.auth_home, name="home"),
    path("google/onetap/", views.google_onetap, name="google_onetap"),
    path("request/", views.request_access, name="request"),
    path("verify/", views.verify_access, name="verify"),
    path("profile/", views.profile_edit, name="profile_edit"),
]
