from django.urls import path

from clientes import views

urlpatterns = [
    path("", views.lista_empresas, name="lista_empresas"),
    path("nova/", views.editar_empresa, name="nova_empresa"),
    path("<int:empresa_id>/", views.editar_empresa, name="editar_empresa"),
    path("<int:empresa_id>/situacao/", views.alternar_status_empresa, name="situacao_empresa"),
    path("<int:empresa_id>/excluir/", views.excluir_empresa, name="excluir_empresa"),
    path("cnpj/<str:cnpj>/", views.buscar_cnpj, name="buscar_cnpj"),
    path("ativar/<int:empresa_id>/", views.definir_empresa, name="definir_empresa"),
    path("competencia/", views.definir_competencia, name="definir_competencia"),
]
