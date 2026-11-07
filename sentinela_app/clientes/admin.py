from django.contrib import admin

from clientes.models import DocumentoXML, Empresa

admin.site.site_header = "Sentinela"
admin.site.site_title = "Sentinela"
admin.site.index_title = "Cadastros"


@admin.register(Empresa)
class EmpresaAdmin(admin.ModelAdmin):
    list_display = ("codigo_dominio", "razao_social", "cnpj", "uf", "regime_tributario", "perfil_estabelecimento", "responsavel_apuracao", "responsavel_revisao", "status")
    search_fields = ("codigo_dominio", "razao_social", "cnpj", "responsavel_apuracao", "responsavel_revisao")


@admin.register(DocumentoXML)
class DocumentoXMLAdmin(admin.ModelAdmin):
    list_display = ("empresa", "competencia", "modelo", "numero_nota", "status_sefa", "tipo_operacao", "possui_divergencia")
    search_fields = ("chave_acesso", "numero_nota", "empresa__razao_social")
    list_filter = ("modelo", "status_sefa", "tipo_operacao", "possui_divergencia")
