from django.contrib import admin

from clientes.models import Colaborador, DocumentoXML, Empresa, InscricaoSubstituto, LogAlteracao, NotaServico

admin.site.site_header = "Sentinela"
admin.site.site_title = "Sentinela"
admin.site.index_title = "Cadastros"


class InscricaoSubstitutoInline(admin.TabularInline):
    model = InscricaoSubstituto
    extra = 0


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("codigo_dominio", "razao_social", "cnpj", "uf", "regime_tributario", "perfil_estabelecimento", "responsavel_apuracao", "responsavel_revisao", "status")
    search_fields = ("codigo_dominio", "razao_social", "cnpj", "responsavel_apuracao__nome", "responsavel_revisao__nome")
    inlines = (InscricaoSubstitutoInline,)


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


@admin.register(NotaServico)
class NotaServicoAdmin(admin.ModelAdmin):
    list_display = ("empresa", "competencia", "tipo", "numero", "razao_terceiro", "valor_servico", "status", "etapa_escrituracao")
    search_fields = ("chave_acesso", "numero", "razao_terceiro", "empresa__razao_social")
    list_filter = ("tipo", "status", "etapa_escrituracao", "possui_alerta")


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
