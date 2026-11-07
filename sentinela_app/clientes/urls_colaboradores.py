from django.urls import path

from clientes import views

urlpatterns = [
    path("", views.lista_colaboradores, name="lista_colaboradores"),
    path("novo/", views.editar_colaborador, name="novo_colaborador"),
    path("<int:colaborador_id>/", views.editar_colaborador, name="editar_colaborador"),
    path("<int:colaborador_id>/situacao/", views.alternar_colaborador, name="situacao_colaborador"),
    path("<int:colaborador_id>/excluir/", views.excluir_colaborador, name="excluir_colaborador"),
]
