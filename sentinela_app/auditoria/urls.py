from django.urls import path

from auditoria import views

urlpatterns = [
    path("", views.painel, name="auditoria"),
    path("sped/", views.cruzar, name="cruzar_sped"),
]
