from django.urls import path

from triagem_xml import views

urlpatterns = [
    path("", views.painel, name="triagem"),
    path("importar/", views.importar, name="importar_xml"),
    path("garimpeiro/", views.garimpeiro, name="garimpeiro"),
    path("garimpeiro/extrair/", views.extrair_garimpeiro, name="extrair_garimpeiro"),
]
