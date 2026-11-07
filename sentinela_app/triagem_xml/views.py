from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa
from triagem_xml.servico import importar_uploads


def _empresa_ativa(request):
    empresa_id = request.session.get("empresa_id")
    if not empresa_id:
        return None
    return Empresa.objects.filter(pk=empresa_id).first()


@login_required
def painel(request):
    request.modulo = "triagem"
    empresa = _empresa_ativa(request)
    documentos = []
    if empresa is not None:
        documentos = DocumentoXML.objects.filter(empresa=empresa, competencia=request.session.get("competencia", ""))
    return render(request, "triagem/painel.html", {"documentos": documentos, "empresa_travada": empresa})


@login_required
@require_POST
def importar(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa antes de importar os XML.")
        return redirect("painel")
    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione ao menos um XML ou um zip.")
        return redirect("triagem")
    try:
        validar_cadastro_para_apuracao(empresa)
        total = importar_uploads(empresa, arquivos, request.session.get("competencia", ""))
    except CadastroIncompleto as erro:
        messages.error(request, str(erro))
        return redirect("triagem")
    messages.success(request, f"Leitura concluída: {total} XML processados na pasta do mês.")
    return redirect("triagem")
