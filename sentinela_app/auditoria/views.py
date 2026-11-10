import tempfile
from pathlib import Path

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from auditoria.apuracao_motor import MotorApuracaoSentinela
from auditoria.sped_motor import MotorSpedCruzamento
from clientes.acesso import exige
from clientes.governance import CadastroIncompleto, avisar_cadastro_incompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa
from clientes.trilha import registrar


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


@exige("pode_auditar")
def painel(request):
    request.modulo = "auditoria"
    empresa = _empresa_ativa(request)
    competencia = request.session.get("competencia", "")
    documentos = DocumentoXML.objects.none()
    divergencias = []
    fechamento = None
    ausentes = request.session.get("sped_ausentes", [])
    if empresa is not None and competencia:
        documentos = DocumentoXML.objects.filter(empresa=empresa, competencia=competencia)
        divergencias = documentos.filter(possui_divergencia=True)
        fechamento = MotorApuracaoSentinela(empresa, competencia).resumo_fechamento_completo(sped_ausentes=ausentes)
    return render(
        request,
        "auditoria/painel.html",
        {
            "empresa_travada": empresa,
            "totais": _totais(documentos),
            "divergencias": divergencias,
            "ausentes": ausentes,
            "fechamento": fechamento,
        },
    )


@require_POST
@exige("pode_auditar")
def cruzar(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa antes do cruzamento.")
        return redirect("painel")
    if empresa.status != "Ativo":
        messages.error(request, "Este cliente está inativo.")
        return redirect("auditoria")
    arquivo = request.FILES.get("sped")
    if arquivo is None:
        messages.error(request, "Selecione o arquivo SPED.")
        return redirect("auditoria")
    try:
        validar_cadastro_para_apuracao(empresa)
    except CadastroIncompleto as erro:
        avisar_cadastro_incompleto(request, erro)
        return redirect("auditoria")
    competencia = request.session.get("competencia", "")
    anteriores = {
        nota.chave_acesso: f"{nota.mensagem_divergencia or ''} | {nota.sugestao_correcao or ''}"
        for nota in DocumentoXML.objects.filter(empresa=empresa, competencia=competencia)
    }
    with tempfile.TemporaryDirectory() as pasta:
        caminho = Path(pasta) / "sped.txt"
        caminho.write_bytes(arquivo.read())
        resultado = MotorSpedCruzamento(empresa, competencia, caminho).executar_cruzamento()
    if resultado["erro"]:
        messages.error(request, resultado["erro"])
        return redirect("auditoria")
    request.session["sped_ausentes"] = resultado["sem_xml_no_sped"]
    for nota in DocumentoXML.objects.filter(empresa=empresa, competencia=competencia):
        novo = f"{nota.mensagem_divergencia or ''} | {nota.sugestao_correcao or ''}"
        registrar(request.user, empresa, "AJUSTE", f"Nota {nota.numero_nota}", anteriores.get(nota.chave_acesso, ""), novo)
    for nota in resultado["sem_xml_no_sped"]:
        registrar(
            request.user,
            empresa,
            "AJUSTE",
            f"C100 {nota['numero']}",
            "",
            "A chave está no SPED e não há XML nesta competência.",
        )
    messages.success(
        request,
        f"Cruzamento feito com {resultado['total_notas_sped']} chaves do C100 e {resultado['total_xmls_lidos']} XML da competência.",
    )
    return redirect("auditoria")
