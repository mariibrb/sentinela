from django.urls import path

from fechamento import views

urlpatterns = [
    path("", views.painel_fechamento, name="fechamento"),
    path("api/empresas/", views.api_empresas, name="fechamento_api_empresas"),
    path("api/revisores/", views.api_revisores, name="fechamento_api_revisores"),
    path("api/estado/", views.api_estado, name="fechamento_api_estado"),
    path("api/estado/salvar/", views.api_salvar_estado, name="fechamento_api_salvar"),
    path("api/aprovar/", views.api_aprovar, name="fechamento_api_aprovar"),
]
