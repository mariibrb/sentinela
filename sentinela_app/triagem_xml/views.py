from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from clientes.acesso import exige
from clientes.competencia import interpretar
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa
from clientes.trilha import registrar
from garimpeiro_legado.consulta import ABAS, ABAS_TELA, montar, planilha, zip_xml
from triagem_xml.servico import importar_uploads


def _empresa_ativa(request):
    empresa_id = request.session.get("empresa_id")
    if not empresa_id:
        return None
    return Empresa.objects.filter(pk=empresa_id).first()


@exige("pode_triar")
def painel(request):
    request.modulo = "triagem"
    empresa = _empresa_ativa(request)
    documentos = []
    if empresa is not None:
        documentos = DocumentoXML.objects.filter(empresa=empresa, competencia=request.session.get("competencia", ""))
    return render(request, "triagem/painel.html", {"documentos": documentos, "empresa_travada": empresa})


@require_POST
@exige("pode_triar")
def importar(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa antes de importar os XML.")
        return redirect("painel")
    if empresa.status != "Ativo":
        messages.error(request, "Este cliente está inativo.")
        return redirect("triagem")
    arquivos = request.FILES.getlist("arquivos")
    if not arquivos:
        messages.error(request, "Selecione ao menos um XML ou um zip.")
        return redirect("triagem")
    try:
        validar_cadastro_para_apuracao(empresa)
        competencia = request.session.get("competencia", "")
        total, garimpo_ok, texto_garimpo = importar_uploads(empresa, arquivos, competencia)
        registrar(request.user, empresa, "TRIAGEM", "Lote de XML", "", f"{total} XML na competência {competencia}")
    except CadastroIncompleto as erro:
        messages.error(request, str(erro))
        return redirect("triagem")
    except Exception:
        messages.error(
            request,
            "A leitura desse lote não terminou. Envie os XML em um ZIP ou divida em partes menores.",
        )
        return redirect("triagem")
    messages.success(request, f"Leitura concluída: {total} XML processados na pasta do mês.")
    if garimpo_ok:
        messages.success(request, f"Garimpeiro: {texto_garimpo}")
    else:
        messages.warning(request, texto_garimpo)
    return redirect("garimpeiro")


def _aba(valor: str) -> str:
    return valor if valor in ABAS else "lidos"


def _periodo(request):
    partes = interpretar(request.session.get("competencia", ""))
    if partes is None:
        return None
    mes, ano = partes
    return ano, mes


@exige("pode_triar")
def garimpeiro(request):
    request.modulo = "garimpeiro"
    empresa = _empresa_ativa(request)
    aba = _aba(request.GET.get("aba", ""))
    painel = None
    periodo = _periodo(request)
    if empresa is not None and periodo is not None:
        ano, mes = periodo
        painel = montar(empresa.cnpj, ano, mes, aba)
    return render(request, "triagem/garimpeiro.html", {
        "empresa_travada": empresa,
        "aba": aba,
        "abas": ABAS_TELA,
        "painel": painel,
        "periodo_ok": periodo is not None,
    })


@exige("pode_triar")
@require_POST
def extrair_garimpeiro(request):
    empresa = _empresa_ativa(request)
    periodo = _periodo(request)
    if empresa is None or periodo is None:
        messages.error(request, "Escolha a empresa e a competência antes de extrair.")
        return redirect("garimpeiro")
    ano, mes = periodo
    aba = _aba(request.POST.get("aba", ""))
    nome = f"garimpeiro_{aba}_{mes:02d}{ano}"
    if request.POST.get("tipo") == "xml":
        conteudo, gravados, sem_arquivo = zip_xml(empresa.cnpj, ano, mes, aba)
        if gravados == 0:
            messages.warning(
                request,
                "Os XML desta leitura ainda não estão num caminho de salvamento. A planilha do que foi lido continua disponível.",
            )
            return redirect(f"/triagem/garimpeiro/?aba={aba}")
        if sem_arquivo:
            messages.warning(request, f"{sem_arquivo} nota(s) ficaram de fora porque o XML não está gravado.")
        resposta = HttpResponse(conteudo, content_type="application/zip")
        resposta["Content-Disposition"] = f'attachment; filename="{nome}.zip"'
        return resposta
    texto = planilha(empresa.cnpj, ano, mes, aba)
    resposta = HttpResponse("\ufeff" + texto, content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = f'attachment; filename="{nome}.csv"'
    return resposta
