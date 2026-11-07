from django.contrib import admin

from clientes.models import Colaborador, DocumentoXML, Empresa, LogAlteracao

admin.site.site_header = "Sentinela"
admin.site.site_title = "Sentinela"
admin.site.index_title = "Cadastros"


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("codigo_dominio", "razao_social", "cnpj", "uf", "regime_tributario", "perfil_estabelecimento", "responsavel_apuracao", "responsavel_revisao", "status")
    search_fields = ("codigo_dominio", "razao_social", "cnpj", "responsavel_apuracao__nome", "responsavel_revisao__nome")


@admin.register(Colaborador)
class ColaboradorAdmin(admin.ModelAdmin):
    list_display = ("nome", "cargo", "email", "usuario", "ativo")
    search_fields = ("nome", "cargo", "email", "usuario__username")
    list_filter = ("ativo", "pode_cadastrar_cliente", "pode_editar_cliente", "pode_inativar_cliente", "pode_excluir_cliente", "pode_triar", "pode_auditar")


@admin.register(DocumentoXML)
class DocumentoXMLAdmin(admin.ModelAdmin):
    list_display = ("empresa", "competencia", "modelo", "numero_nota", "status_sefa", "tipo_operacao", "possui_divergencia")
    search_fields = ("chave_acesso", "numero_nota", "empresa__razao_social")
    list_filter = ("modelo", "status_sefa", "tipo_operacao", "possui_divergencia")


@admin.register(LogAlteracao)
class LogAlteracaoAdmin(admin.ModelAdmin):
    list_display = ("quando", "usuario", "empresa", "acao", "campo")
    search_fields = ("campo", "valor_anterior", "valor_novo", "empresa__razao_social")
    list_filter = ("acao",)
    readonly_fields = ("usuario", "quando", "empresa", "acao", "campo", "valor_anterior", "valor_novo")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
