import tempfile
from pathlib import Path

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from auditoria.sped_motor import MotorSpedCruzamento
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa


def _empresa_ativa(request):
    empresa_id = request.session.get("empresa_id")
    if not empresa_id:
        return None
    return Empresa.objects.filter(pk=empresa_id).first()


def _totais(documentos):
    nomes = (
        "valor_total", "base_icms", "valor_icms", "base_icms_st", "valor_icms_st",
        "valor_ipi", "valor_pis", "valor_cofins",
    )
    agregados = documentos.filter(status_sefa="AUTORIZADA").aggregate(**{nome: Sum(nome) for nome in nomes})
    return {nome: agregados[nome] or 0 for nome in nomes}


@login_required
def painel(request):
    request.modulo = "auditoria"
    empresa = _empresa_ativa(request)
    documentos = DocumentoXML.objects.none()
    divergencias = []
    if empresa is not None:
        documentos = DocumentoXML.objects.filter(empresa=empresa, competencia=request.session.get("competencia", ""))
        divergencias = documentos.filter(possui_divergencia=True)
    return render(
        request,
        "auditoria/painel.html",
        {
            "empresa_travada": empresa,
            "totais": _totais(documentos),
            "divergencias": divergencias,
            "ausentes": request.session.get("sped_ausentes", []),
        },
    )


@login_required
@require_POST
def cruzar(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa antes do cruzamento.")
        return redirect("painel")
    arquivo = request.FILES.get("sped")
    if arquivo is None:
        messages.error(request, "Selecione o arquivo SPED.")
        return redirect("auditoria")
    try:
        validar_cadastro_para_apuracao(empresa)
    except CadastroIncompleto as erro:
        messages.error(request, str(erro))
        return redirect("auditoria")
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "sped.txt"
        caminho.write_bytes(arquivo.read())
        resultado = MotorSpedCruzamento(empresa, request.session.get("competencia", ""), caminho).executar_cruzamento()
    if resultado["erro"]:
        messages.error(request, resultado["erro"])
        return redirect("auditoria")
    request.session["sped_ausentes"] = resultado["sem_xml_no_sped"]
    messages.success(
        request,
        f"Cruzamento feito com {resultado['total_notas_sped']} chaves do C100 e {resultado['total_xmls_lidos']} XML da competência.",
    )
    return redirect("auditoria")
