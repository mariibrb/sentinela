import io
import os
import secrets
from pathlib import Path

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from auditoria.apuracao_motor import MotorApuracaoSentinela
from clientes.acesso import apenas_administrador, exige
from clientes.competencia import deslocar, interpretar, rotulo
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa, NotaServico
from clientes.trilha import registrar
from clientes.utils_sharepoint import pasta_do_mes, pasta_relatorios
from garimpeiro_legado.consulta import ABAS, ABAS_TELA, montar, planilha, planilha_xlsx_bytes, zip_xml
from triagem_xml.coletor_nfse import (
    GERENCIADOR_COLETOR,
    VERSAO_COLETOR,
    gravar_planilha_empresa,
    importar_planilha_coletor,
    importar_xml_nfse,
    periodo_do_mes,
    periodo_livre,
)
from triagem_xml.fatias import avancar, token_valido
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


def _importar_em_partes(request, empresa):
    """Cada resposta grava um punhado de notas e volta, para o lote não derrubar o site."""
    try:
        validar_cadastro_para_apuracao(empresa)
    except CadastroIncompleto as erro:
        return JsonResponse({"ok": False, "mensagem": str(erro)}, status=400)
    token = (request.POST.get("token") or "").strip()
    arquivos = request.FILES.getlist("arquivos")
    if token and not token_valido(token):
        return JsonResponse({"ok": False, "mensagem": "Esta parte da leitura não foi encontrada. Envie o lote de novo."}, status=400)
    if not token and not arquivos:
        return JsonResponse({"ok": False, "mensagem": "Selecione ao menos um XML ou um zip."}, status=400)
    if not token:
        token = secrets.token_hex(8)
    try:
        resultado = avancar(
            empresa,
            request.session.get("competencia", ""),
            request.user.pk,
            token,
            arquivos,
        )
    except Exception:
        return JsonResponse(
            {"ok": False, "mensagem": "Esta parte não terminou. O que já foi lido permanece. Mande o restante de novo."},
            status=500,
        )
    if request.POST.get("fim") == "1" and not resultado["continua"]:
        registrar(request.user, empresa, "TRIAGEM", "Lote de XML", "", "Leitura em partes concluída")
        messages.success(request, "Leitura concluída. As notas desta competência estão no Garimpeiro.")
    return JsonResponse({"ok": True, "mensagem": "", **resultado})


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
    if request.POST.get("resposta") == "json":
        return _importar_em_partes(request, empresa)
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
    import json

    request.modulo = "garimpeiro"
    empresa = _empresa_ativa(request)
    aba = _aba(request.GET.get("aba", ""))
    try:
        pagina = int(request.GET.get("page") or 1)
    except (TypeError, ValueError):
        pagina = 1
    painel_dados = None
    periodo = _periodo(request)
    grade_json = "[]"
    colunas_json = "[]"
    if empresa is not None and periodo is not None:
        painel_dados = montar(empresa, request.session.get("competencia", ""), aba, pagina=pagina)
        grade_json = json.dumps(painel_dados.get("linhas_grade", []), ensure_ascii=False)
        colunas_json = json.dumps(painel_dados.get("colunas", []), ensure_ascii=False)
    return render(request, "triagem/garimpeiro.html", {
        "empresa_travada": empresa,
        "aba": aba,
        "abas": ABAS_TELA,
        "painel": painel_dados,
        "grade_json": grade_json,
        "colunas_json": colunas_json,
        "periodo_ok": periodo is not None,
    })


@exige("pode_triar")
@require_POST
def extrair_garimpeiro(request):
    import re

    empresa = _empresa_ativa(request)
    periodo = _periodo(request)
    if empresa is None or periodo is None:
        messages.error(request, "Escolha a empresa e a competência antes de extrair.")
        return redirect("garimpeiro")
    ano, mes = periodo
    competencia = request.session.get("competencia", "")
    aba = _aba(request.POST.get("aba", ""))
    tipo = (request.POST.get("tipo") or "xlsx").strip().lower()
    chaves_brutas = request.POST.get("chaves") or ""
    chaves_lista = re.findall(r"\d{44}", chaves_brutas)

    nome = f"garimpeiro_{empresa.codigo_dominio}_{aba}_{mes:02d}{ano}"

    if tipo in {"xml", "xml_chaves", "xml_recorte"}:
        filtros_recorte = None
        if tipo == "xml_recorte":
            filtros_recorte = {
                "origem": request.POST.get("recorte_origem", ""),
                "status": request.POST.get("recorte_status", ""),
                "serie": request.POST.get("recorte_serie", ""),
                "modelo": request.POST.get("recorte_modelo", ""),
                "nota_ini": request.POST.get("recorte_nota_ini", ""),
                "nota_fim": request.POST.get("recorte_nota_fim", ""),
                "data_ini": request.POST.get("recorte_data_ini", ""),
                "data_fim": request.POST.get("recorte_data_fim", ""),
            }
            nome = f"garimpeiro_recorte_{empresa.codigo_dominio}_{mes:02d}{ano}"
        elif tipo == "xml_chaves" and chaves_lista:
            nome = f"garimpeiro_selecao_{len(chaves_lista)}_xml_{mes:02d}{ano}"

        conteudo, gravados, sem_arquivo = zip_xml(
            empresa,
            competencia,
            aba,
            chaves_especificas=chaves_lista if chaves_lista else None,
            filtros_recorte=filtros_recorte,
        )
        if gravados == 0:
            messages.warning(
                request,
                "Os XML desta seleção ainda não estão gravados na pasta ou nenhuma nota coincidiu com o filtro.",
            )
            return redirect(f"/triagem/garimpeiro/?aba={aba}")
        if sem_arquivo:
            messages.warning(request, f"{sem_arquivo} nota(s) ficaram de fora porque o XML não está gravado.")
        resposta = HttpResponse(conteudo, content_type="application/zip")
        resposta["Content-Disposition"] = f'attachment; filename="{nome}.zip"'
        return resposta

    if tipo in {"xlsx", "xlsx_completo"}:
        aba_alvo = "completo" if tipo == "xlsx_completo" else aba
        sufixo = "completo" if tipo == "xlsx_completo" else aba
        nome_xlsx = f"garimpeiro_{empresa.codigo_dominio}_{sufixo}_{mes:02d}{ano}.xlsx"
        dados_xlsx = planilha_xlsx_bytes(
            empresa,
            competencia,
            aba=aba_alvo,
            chaves_filtro=set(chaves_lista) if chaves_lista else None,
        )
        resposta = HttpResponse(
            dados_xlsx,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resposta["Content-Disposition"] = f'attachment; filename="{nome_xlsx}"'
        return resposta

    texto = planilha(empresa, competencia, aba)
    resposta = HttpResponse("\ufeff" + texto, content_type="text/csv; charset=utf-8")
    resposta["Content-Disposition"] = f'attachment; filename="{nome}.csv"'
    return resposta


# ============================================================================
# Coletor de NFS-e v1.4.5 Integrado ao Sentinela
# ============================================================================

@exige("pode_triar")
def coletor_nfse(request):
    request.modulo = "coletor_nfse"
    empresa = _empresa_ativa(request)
    competencia = request.session.get("competencia", "")
    partes = interpretar(competencia)
    periodo_sugerido = None
    if partes is not None:
        mes, ano = partes
        periodo_sugerido = periodo_do_mes(mes, ano)

    aba_nfse = (request.GET.get("aba") or "todas").strip().lower()
    notas_prestadas = []
    notas_tomadas = []
    resumo_nfse = None
    caminho_pasta_cliente = ""

    if empresa is not None and partes is not None:
        todas_da_comp = NotaServico.objects.filter(empresa=empresa, competencia=competencia)
        notas_prestadas = list(todas_da_comp.filter(tipo="PRESTADO"))
        notas_tomadas = list(todas_da_comp.filter(tipo="TOMADO"))
        resumo_nfse = MotorApuracaoSentinela(empresa, competencia).calcular_apuracao_nfse()
        try:
            caminho_pasta_cliente = str(
                pasta_do_mes(empresa, competencia) / "02_XML_PROCESSADOS_SENTINELA" / "03_NFSE_PADRAO_NACIONAL"
            )
        except Exception:
            caminho_pasta_cliente = ""

    return render(
        request,
        "triagem/coletor_nfse.html",
        {
            "versao_coletor": VERSAO_COLETOR,
            "empresa_travada": empresa,
            "periodo_sugerido": periodo_sugerido,
            "estado_coletor": GERENCIADOR_COLETOR.estado_atual(),
            "aba_nfse": aba_nfse,
            "notas_prestadas": notas_prestadas,
            "notas_tomadas": notas_tomadas,
            "resumo_nfse": resumo_nfse,
            "caminho_pasta_cliente": caminho_pasta_cliente,
            "proxima_competencia_rotulo": rotulo(deslocar(competencia, 1)) if partes else "Mês seguinte",
        },
    )


@require_POST
@exige("pode_triar")
def iniciar_coletor_nfse(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa no topo do Sentinela antes de iniciar o Apanhador 1.4.5.")
        return redirect("coletor_nfse")
    if empresa.status != "Ativo":
        messages.error(request, "Este cliente está inativo.")
        return redirect("coletor_nfse")

    competencia = request.session.get("competencia", "")
    partes = interpretar(competencia)
    if partes is None:
        messages.error(request, "Defina uma competência válida no topo antes de iniciar o Apanhador.")
        return redirect("coletor_nfse")

    modo = (request.POST.get("modo_periodo") or "mes").strip()
    try:
        if modo == "livre":
            data_ini = (request.POST.get("data_inicial") or "").strip()
            data_fim = (request.POST.get("data_final") or "").strip()
            periodo = periodo_livre(data_ini, data_fim)
        else:
            mes, ano = partes
            periodo = periodo_do_mes(mes, ano)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("coletor_nfse")

    from triagem_xml.chrome_portal import URL_LOGIN, caminho_do_chrome, motivo_sem_chrome

    if not caminho_do_chrome():
        motivo = motivo_sem_chrome()
        GERENCIADOR_COLETOR.escrever_log(
            f"Portal Nacional aberto no navegador para {empresa.codigo_dominio} · {empresa.razao_social} ({periodo.inicio:%d/%m/%Y} a {periodo.fim:%d/%m/%Y})."
        )
        GERENCIADOR_COLETOR.escrever_log(motivo)
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.POST.get("resposta") == "json":
            return JsonResponse({"ok": True, "abrir_url": URL_LOGIN, "mensagem": motivo, "estado": GERENCIADOR_COLETOR.estado_atual()})
        messages.info(request, motivo)
        return redirect("coletor_nfse")

    ok, msg = GERENCIADOR_COLETOR.iniciar_coleta(
        empresa=empresa,
        competencia=competencia,
        periodo=periodo,
        usuario=request.user,
    )
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.POST.get("resposta") == "json":
        return JsonResponse({"ok": ok, "mensagem": msg, "estado": GERENCIADOR_COLETOR.estado_atual()})
    if ok:
        messages.success(request, msg)
    else:
        messages.warning(request, msg)
    return redirect("coletor_nfse")


@require_POST
@exige("pode_triar")
def parar_coletor_nfse(request):
    GERENCIADOR_COLETOR.pedir_parar()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.POST.get("resposta") == "json":
        return JsonResponse({"ok": True, "estado": GERENCIADOR_COLETOR.estado_atual()})
    messages.info(request, "Solicitação de parada enviada ao Apanhador 1.4.5.")
    return redirect("coletor_nfse")


@exige("pode_triar")
def status_coletor_nfse(request):
    return JsonResponse(GERENCIADOR_COLETOR.estado_atual())


@require_POST
@apenas_administrador
def importar_relatorio_nfse(request):
    empresa = _empresa_ativa(request)
    if empresa is None:
        messages.error(request, "Escolha a empresa ativa antes de importar relatórios do Apanhador.")
        return redirect("coletor_nfse")
    competencia = request.session.get("competencia", "")
    if interpretar(competencia) is None:
        messages.error(request, "Escolha a competência antes de importar.")
        return redirect("coletor_nfse")

    arquivos = request.FILES.getlist("arquivos_nfse")
    if not arquivos:
        messages.error(request, "Selecione ao menos uma planilha Excel (.xlsx) ou arquivo XML de NFS-e.")
        return redirect("coletor_nfse")

    tipo_forcado = (request.POST.get("tipo_relatorio") or "").strip().lower()
    total_gravadas = 0
    erros = []

    for arq in arquivos:
        nome = Path(arq.name).name
        baixo = nome.lower()
        conteudo = arq.read()
        if baixo.endswith(".xlsx"):
            res = importar_planilha_coletor(
                empresa=empresa,
                competencia=competencia,
                arquivo_bytes=conteudo,
                nome_arquivo=nome,
                tipo_forcado=tipo_forcado,
            )
            if res.get("ok"):
                total_gravadas += res.get("gravadas", 0)
            else:
                erros.append(f"{nome}: {res.get('erro')}")
        elif baixo.endswith(".xml"):
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
                tmp.write(conteudo)
                tmp_path = Path(tmp.name)
            try:
                lida = importar_xml_nfse(empresa, competencia, tmp_path)
                if lida is not None:
                    total_gravadas += 1
                else:
                    erros.append(f"{nome}: XML não pertence ao CNPJ de {empresa.razao_social} ou não é uma NFS-e.")
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            erros.append(f"{nome}: formato não suportado (use .xlsx ou .xml).")

    if total_gravadas:
        registrar(
            request.user,
            empresa,
            "COLETOR",
            "Importação de Relatório NFS-e",
            "",
            f"{total_gravadas} NFS-e vinculadas na competência {competencia}",
        )
        messages.success(
            request,
            f"{total_gravadas} NFS-e importada(s) e armazenada(s) no cliente {empresa.codigo_dominio} · {empresa.razao_social}!",
        )
    for err in erros:
        messages.error(request, err)
    return redirect("coletor_nfse")


@require_POST
@exige("pode_triar")
def exportar_relatorio_nfse(request):
    empresa = _empresa_ativa(request)
    competencia = request.session.get("competencia", "")
    if empresa is None or interpretar(competencia) is None:
        messages.error(request, "Escolha a empresa e a competência antes de baixar o relatório.")
        return redirect("coletor_nfse")

    tipo = (request.POST.get("tipo") or "prestados").strip().lower()
    tipo_db = "PRESTADO" if tipo == "prestados" else "TOMADO"
    qs = NotaServico.objects.filter(empresa=empresa, competencia=competencia, tipo=tipo_db)
    if not qs.exists():
        messages.warning(request, "Não há notas desse tipo armazenadas nesta competência para gerar a planilha.")
        return redirect("coletor_nfse")

    notas_dicts = [
        {
            "numero": n.numero,
            "chave_acesso": n.chave_acesso,
            "data_emissao": n.data_emissao.strftime("%d/%m/%Y") if n.data_emissao else n.data_emissao_texto,
            "competencia_emissao": n.competencia_emissao,
            "razao_emitente": n.razao_terceiro,
            "codigo_tributacao_nacional": n.codigo_tributacao_nacional,
            "valor_servico": float(n.valor_servico),
            "valor_liquido": float(n.valor_liquido),
            "retencao": n.retencao,
            "valor_issqn": float(n.valor_issqn),
            "contrib_sociais_retidas": float(n.contrib_sociais_retidas),
            "contrib_previdenciaria_retida": float(n.contrib_previdenciaria_retida),
            "irrf": float(n.irrf),
            "status": n.status,
            "data_cancelamento": n.data_cancelamento,
        }
        for n in qs
    ]
    pasta_rel = pasta_relatorios(empresa, competencia)
    rotulo_tipo = "Prestadas" if tipo == "prestados" else "Tomadas"
    nome_arq = f"Relatório NFS-e {rotulo_tipo} - {str(empresa.codigo_dominio).zfill(4)}_{empresa.cnpj}_{competencia}.xlsx"
    destino = pasta_rel / nome_arq
    gravar_planilha_empresa(
        notas_dicts,
        destino,
        rotulo_periodo=rotulo(competencia),
        tipo=tipo,
        empresa=empresa,
    )
    resposta = HttpResponse(
        destino.read_bytes(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resposta["Content-Disposition"] = f'attachment; filename="{nome_arq}"'
    return resposta


@require_POST
@exige("pode_triar")
def abrir_pasta_nfse(request):
    empresa = _empresa_ativa(request)
    competencia = request.session.get("competencia", "")
    if empresa is None or interpretar(competencia) is None:
        messages.error(request, "Escolha a empresa e a competência.")
        return redirect("coletor_nfse")
    pasta = pasta_do_mes(empresa, competencia) / "02_XML_PROCESSADOS_SENTINELA" / "03_NFSE_PADRAO_NACIONAL"
    pasta.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        try:
            os.startfile(str(pasta))
            messages.success(request, f"Pasta aberta no Windows Explorer: {pasta}")
        except Exception as e:
            messages.warning(request, f"Caminho da pasta: {pasta} ({e})")
    else:
        messages.info(request, f"Caminho da pasta do cliente: {pasta}")
    return redirect("coletor_nfse")


@require_POST
@exige("pode_triar")
def alterar_etapa_nfse(request, nota_id: int):
    empresa = _empresa_ativa(request)
    if empresa is None:
        return redirect("coletor_nfse")
    nota = get_object_or_404(NotaServico, pk=nota_id, empresa=empresa)
    nova_etapa = (request.POST.get("etapa") or "").strip()
    destino_redirect = request.POST.get("next") or "coletor_nfse"
    if nova_etapa in {"PENDENTE", "ESCRITURADA", "MES_SEGUINTE", "NAO_ESCRITURAR"}:
        anterior = nota.get_etapa_escrituracao_display()
        nota.etapa_escrituracao = nova_etapa
        if nova_etapa == "MES_SEGUINTE":
            nota.competencia_escrituracao = deslocar(nota.competencia, 1)
        else:
            nota.competencia_escrituracao = nota.competencia
        nota.save(update_fields=["etapa_escrituracao", "competencia_escrituracao", "atualizado_em"])
        registrar(
            request.user,
            empresa,
            "AJUSTE",
            f"NFS-e Nº {nota.numero}",
            anterior,
            nota.get_etapa_escrituracao_display(),
        )
        messages.success(
            request,
            f"NFS-e Nº {nota.numero} atualizada para: {nota.get_etapa_escrituracao_display()}.",
        )
    return redirect(destino_redirect)


@require_POST
@exige("pode_triar")
def marcar_lote_nfse(request):
    empresa = _empresa_ativa(request)
    competencia = request.session.get("competencia", "")
    if empresa is None or not competencia:
        return redirect("coletor_nfse")
    acao = (request.POST.get("acao") or "escriturar_todas").strip()
    destino_redirect = request.POST.get("next") or "coletor_nfse"
    if acao == "escriturar_todas":
        qtd = (
            NotaServico.objects.filter(
                empresa=empresa,
                competencia=competencia,
                etapa_escrituracao="PENDENTE",
            )
            .exclude(status__iexact="Cancelada")
            .update(etapa_escrituracao="ESCRITURADA", competencia_escrituracao=competencia)
        )
        messages.success(request, f"{qtd} NFS-e ativa(s) marcada(s) como Escriturada(s) neste mês!")
    elif acao == "reabrir_todas":
        qtd = (
            NotaServico.objects.filter(
                empresa=empresa,
                competencia=competencia,
                etapa_escrituracao="ESCRITURADA",
            )
            .update(etapa_escrituracao="PENDENTE")
        )
        messages.info(request, f"{qtd} NFS-e voltou/voltaram para 'Falta escriturar / conferir'.")
    return redirect(destino_redirect)
