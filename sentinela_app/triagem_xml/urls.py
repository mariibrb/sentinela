from django.urls import path

from triagem_xml import views

urlpatterns = [
    path("", views.painel, name="triagem"),
    path("importar/", views.importar, name="importar_xml"),
]
