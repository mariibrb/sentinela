from django.shortcuts import redirect
from django.urls import path

from triagem_xml import views

urlpatterns = [
    path("", views.painel, name="triagem"),
    path("importar/", views.importar, name="importar_xml"),
    path("garimpeiro/", views.garimpeiro, name="garimpeiro"),
    path("garimpeiro/extrair/", views.extrair_garimpeiro, name="extrair_garimpeiro"),
    path("apanhador/", views.coletor_nfse, name="coletor_nfse"),
    path("apanhador/", views.coletor_nfse, name="apanhador_nfse"),
    path("apanhador/iniciar/", views.iniciar_coletor_nfse, name="iniciar_coletor_nfse"),
    path("apanhador/parar/", views.parar_coletor_nfse, name="parar_coletor_nfse"),
    path("apanhador/status/", views.status_coletor_nfse, name="status_coletor_nfse"),
    path("apanhador/importar/", views.importar_relatorio_nfse, name="importar_relatorio_nfse"),
    path("apanhador/exportar/", views.exportar_relatorio_nfse, name="exportar_relatorio_nfse"),
    path("apanhador/abrir-pasta/", views.abrir_pasta_nfse, name="abrir_pasta_nfse"),
    path("apanhador/nota/<int:nota_id>/etapa/", views.alterar_etapa_nfse, name="alterar_etapa_nfse"),
    path("apanhador/marcar-lote/", views.marcar_lote_nfse, name="marcar_lote_nfse"),
    path("coletor/", lambda _request: redirect("coletor_nfse", permanent=False)),
]
