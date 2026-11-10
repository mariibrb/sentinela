from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, ProtectedError, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from auditoria.apuracao_motor import MotorApuracaoSentinela
from clientes.competencia import deslocar, montar
from clientes.forms import EmpresaForm
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.acesso import administrador, apenas_administrador, exige, pode
from clientes.backup import exportar_pacote
from clientes.forms import ColaboradorForm
from clientes.models import Colaborador, DocumentoXML, Empresa, LogAlteracao
from clientes.pastas import criar_pastas
from clientes.services import ConsultaIndisponivel, ServicoConsultaCNPJ
from clientes.trilha import foto_empresa, registrar, registrar_empresa


@login_required
def painel(request):
    request.modulo = "painel"
    empresa = None
    empresa_id = request.session.get("empresa_id")
    if empresa_id:
        empresa = Empresa.objects.filter(pk=empresa_id).first()
    competencia = request.session.get("competencia", "")
    bloqueio = ""
    resumo_icms = None
    pis = None
    resumo_nfse = None
    fechamento = None
    notas_divergentes = []
    if empresa and competencia:
        notas_divergentes = DocumentoXML.objects.filter(empresa=empresa, competencia=competencia, possui_divergencia=True)
        motor_geral = MotorApuracaoSentinela(empresa, competencia)
        resumo_nfse = motor_geral.calcular_apuracao_nfse()
        fechamento = motor_geral.resumo_fechamento_completo(sped_ausentes=request.session.get("sped_ausentes", []))
        try:
            validar_cadastro_para_apuracao(empresa)
            resumo_icms = motor_geral.calcular_apuracao_icms()
            pis = motor_geral.calcular_pis_cofins()
        except CadastroIncompleto as erro:
            bloqueio = str(erro)
    return render(
        request,
        "painel.html",
        {
            "empresas": Empresa.objects.all(),
            "bloqueio": bloqueio,
            "resumo_icms": resumo_icms,
            "pis": pis,
            "resumo_nfse": resumo_nfse,
            "fechamento": fechamento,
            "notas_divergentes": notas_divergentes,
        },
    )


@login_required
@require_POST
def definir_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    request.session["empresa_id"] = empresa.pk
    messages.success(request, f"Empresa ativa: {empresa.codigo_dominio} · {empresa.razao_social}.")
    return redirect(request.POST.get("next") or "painel")


@login_required
@require_POST
def definir_competencia(request):
    atual = request.session.get("competencia", "")
    try:
        if request.POST.get("delta"):
            competencia = deslocar(atual, int(request.POST["delta"]))
        else:
            competencia = montar(int(request.POST["mes"]), int(request.POST["ano"]))
    except (TypeError, ValueError):
        competencia = atual
    if competencia:
        request.session["competencia"] = competencia
    destino = request.POST.get("next") or "/"
    if not destino.startswith("/") or destino.startswith("//"):
        destino = "/"
    return redirect(destino)


@login_required
def lista_empresas(request):
    request.modulo = "clientes"
    termo = request.GET.get("q", "").strip()
    filtro_escopo = request.GET.get("escopo", "").strip()
    empresas = Empresa.objects.annotate(notas=Count("documentos"))
    if termo:
        digitos = "".join(caractere for caractere in termo if caractere.isdigit())
        empresas = empresas.filter(
            Q(codigo_dominio__icontains=termo)
            | Q(razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
            | Q(cnpj__icontains=digitos or termo)
            | Q(busca_escopo__icontains=termo)
            | Q(resumo_cliente__icontains=termo)
            | Q(cnae_principal__icontains=termo)
        )
    if filtro_escopo:
        empresas = empresas.filter(busca_escopo__icontains=filtro_escopo)
    from clientes.escopo import ITENS_CONTABEIS, ITENS_DP, ITENS_FISCAIS, ITENS_REGULARIZACAO

    opcoes_filtro_escopo = [
        ("Compliance Fiscal", ITENS_FISCAIS),
        ("Contabilidade", ITENS_CONTABEIS),
        ("Departamento Pessoal", ITENS_DP),
        ("Regularização / Abertura", ITENS_REGULARIZACAO),
    ]
    return render(
        request,
        "clientes/lista.html",
        {
            "empresas": empresas,
            "termo": termo,
            "filtro_escopo": filtro_escopo,
            "opcoes_filtro_escopo": opcoes_filtro_escopo,
        },
    )


@login_required
def editar_empresa(request, empresa_id=None):
    import json
    from clientes.escopo import (
        COLUNA_1_FISCAL,
        COLUNA_2_FISCAL,
        COLUNA_3_FISCAL,
        COLUNA_4_FISCAL,
        ESTADOS_BR,
        ITENS_CONTABEIS,
        ITENS_DP,
        ITENS_FISCAIS,
        ITENS_REGULARIZACAO,
        OPCOES_TTS,
        SETORES_CONTATO,
    )

    request.modulo = "clientes"
    codigo = "pode_editar_cliente" if empresa_id else "pode_cadastrar_cliente"
    if not pode(request.user, codigo):
        messages.error(request, "Seu usuário não tem permissão para esta ação.")
        return redirect("lista_empresas")
    empresa = get_object_or_404(Empresa, pk=empresa_id) if empresa_id else None
    form = EmpresaForm(request.POST or None, request.FILES or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        antes = foto_empresa(empresa)
        antes_substituicoes = empresa.texto_substituicoes() if empresa else ""
        empresa = form.save()
        registrar_empresa(request.user, antes, empresa)
        registrar(
            request.user,
            empresa,
            "CADASTRO",
            "Inscrições de substituto",
            antes_substituicoes,
            empresa.texto_substituicoes(),
        )
        raiz = criar_pastas(empresa)
        messages.success(request, f"Cadastro e escopo de trabalho gravados. Pastas criadas em {raiz}.")
        return redirect("lista_empresas")

    instancia = form.instance
    contatos_atuais = instancia.contatos if isinstance(getattr(instancia, "contatos", None), list) else []
    escopo_fisc = instancia.escopo_fiscal if isinstance(getattr(instancia, "escopo_fiscal", None), dict) else {}
    escopo_cont = instancia.escopo_contabil if isinstance(getattr(instancia, "escopo_contabil", None), dict) else {}
    escopo_dp = instancia.escopo_dp if isinstance(getattr(instancia, "escopo_dp", None), dict) else {}
    escopo_reg = instancia.escopo_regularizacao if isinstance(getattr(instancia, "escopo_regularizacao", None), dict) else {}

    def _montar_itens(lista, mapa):
        return [{"nome": item, "marcado": bool(mapa.get(item))} for item in lista]

    aba_inicial = "escopo" if request.GET.get("aba") == "escopo" else "informacoes"

    return render(
        request,
        "clientes/formulario.html",
        {
            "form": form,
            "empresa": empresa,
            "aba_inicial": aba_inicial,
            "setores_contato": SETORES_CONTATO,
            "contatos_json": json.dumps(contatos_atuais, ensure_ascii=False),
            "colunas_fiscais": [
                _montar_itens(COLUNA_1_FISCAL, escopo_fisc),
                _montar_itens(COLUNA_2_FISCAL, escopo_fisc),
                _montar_itens(COLUNA_3_FISCAL, escopo_fisc),
                _montar_itens(COLUNA_4_FISCAL, escopo_fisc),
            ],
            "itens_contabeis": _montar_itens(ITENS_CONTABEIS, escopo_cont),
            "itens_dp": _montar_itens(ITENS_DP, escopo_dp),
            "itens_regularizacao": _montar_itens(ITENS_REGULARIZACAO, escopo_reg),
            "opcoes_tts": [
                {"nome": op, "marcado": op in (getattr(instancia, "tipos_icms_incentivado", None) or [])}
                for op in OPCOES_TTS
            ],
            "estados_icms_st": [
                {"uf": uf, "marcado": uf in (getattr(instancia, "estados_icms_st", None) or [])}
                for uf in ESTADOS_BR
            ],
            "estados_st": [
                {"uf": uf, "marcado": uf in (getattr(instancia, "estados_st", None) or [])}
                for uf in ESTADOS_BR
            ],
        },
    )


@require_POST
@exige("pode_inativar_cliente")
def alternar_status_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    anterior = empresa.status
    empresa.status = "Inativo" if empresa.status == "Ativo" else "Ativo"
    empresa.save(update_fields=["status"])
    registrar(request.user, empresa, "CADASTRO", "status", anterior, empresa.status)
    messages.success(request, f"{empresa.razao_social} está {empresa.status.lower()}.")
    return redirect("lista_empresas")


@require_POST
@exige("pode_excluir_cliente")
def excluir_empresa(request, empresa_id):
    empresa = get_object_or_404(Empresa, pk=empresa_id)
    nome = empresa.razao_social
    if request.session.get("empresa_id") == empresa.pk:
        request.session.pop("empresa_id", None)
    codigo = empresa.codigo_dominio
    empresa.delete()
    registrar(request.user, None, "EXCLUSAO", f"Cliente {codigo}", nome, "excluído")
    messages.success(request, f"Cliente excluído: {nome}.")
    return redirect("lista_empresas")


@login_required
def buscar_cnpj(request, cnpj):
    try:
        dados = ServicoConsultaCNPJ.dados_cadastrais(cnpj)
    except ValidationError as erro:
        return JsonResponse({"erro": " ".join(erro.messages), "manual": True}, status=400)
    except ConsultaIndisponivel as erro:
        return JsonResponse({"erro": str(erro), "manual": True})
    except Exception:
        return JsonResponse({"erro": "Consulta automática indisponível no momento", "manual": True})
    substituicoes = dados.pop("substituicoes", {})
    return JsonResponse({"campos": dados, "substituicoes": substituicoes})


def _empresa_da_sessao(request):
    empresa_id = request.session.get("empresa_id")
    if not empresa_id:
        return None
    return Empresa.objects.filter(pk=empresa_id).first()


@login_required
def historico(request):
    request.modulo = "historico"
    empresa = _empresa_da_sessao(request)
    logs = LogAlteracao.objects.select_related("usuario", "empresa")
    if empresa is not None:
        logs = logs.filter(empresa=empresa)
    elif not administrador(request.user):
        logs = logs.none()
    return render(request, "clientes/historico.html", {"logs": logs[:200], "empresa_travada": empresa})


@login_required
@require_POST
def exportar_sharepoint(request):
    empresa = _empresa_da_sessao(request)
    competencia = request.session.get("competencia", "")
    if empresa is None or not competencia:
        messages.error(request, "Escolha a empresa e a competência antes de atualizar o acervo.")
        return redirect("historico")
    try:
        destino = exportar_pacote(empresa, competencia)
    except ValueError as erro:
        messages.error(request, str(erro))
        return redirect("historico")
    registrar(
        request.user,
        empresa,
        "BACKUP",
        "Pacote do mês",
        "",
        str(destino),
    )
    messages.success(request, f"Acervo atualizado em {destino}.")
    return redirect("historico")


@apenas_administrador
def area_restrita(request):
    request.modulo = "restrita"
    return render(request, "restrita.html")


@apenas_administrador
def lista_colaboradores(request):
    request.modulo = "colaboradores"
    return render(request, "colaboradores/lista.html", {"colaboradores": Colaborador.objects.all()})


@apenas_administrador
def editar_colaborador(request, colaborador_id=None):
    request.modulo = "colaboradores"
    colaborador = get_object_or_404(Colaborador, pk=colaborador_id) if colaborador_id else None
    form = ColaboradorForm(request.POST or None, instance=colaborador)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Colaborador gravado.")
        return redirect("lista_colaboradores")
    feitos = []
    if colaborador is not None and colaborador.usuario_id:
        feitos = LogAlteracao.objects.filter(usuario_id=colaborador.usuario_id).select_related("empresa")[:150]
    return render(
        request,
        "colaboradores/formulario.html",
        {"form": form, "colaborador": colaborador, "feitos": feitos},
    )


@require_POST
@apenas_administrador
def alternar_colaborador(request, colaborador_id):
    pessoa = get_object_or_404(Colaborador, pk=colaborador_id)
    pessoa.ativo = not pessoa.ativo
    pessoa.save(update_fields=["ativo"])
    situacao = "ativo" if pessoa.ativo else "inativo"
    messages.success(request, f"{pessoa.nome} está {situacao}.")
    return redirect("lista_colaboradores")


@require_POST
@apenas_administrador
def excluir_colaborador(request, colaborador_id):
    pessoa = get_object_or_404(Colaborador, pk=colaborador_id)
    nome = pessoa.nome
    try:
        pessoa.delete()
    except ProtectedError:
        messages.error(
            request,
            f"{nome} é responsável pela apuração ou pela revisão de um cliente. Troque o responsável ou inative o colaborador.",
        )
        return redirect("lista_colaboradores")
    registrar(request.user, None, "EXCLUSAO", "Colaborador", nome, "excluído")
    messages.success(request, f"Colaborador excluído: {nome}.")
    return redirect("lista_colaboradores")


@apenas_administrador
def baixar_planilha_clientes(request):
    from clientes.lote_planilha import gerar_planilha_modelo_clientes

    incluir = request.GET.get("vazia") != "1"
    conteudo = gerar_planilha_modelo_clientes(incluir_existentes=incluir)
    resposta = HttpResponse(
        conteudo,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resposta["Content-Disposition"] = 'attachment; filename="Sentinela_Clientes_Lote.xlsx"'
    return resposta


@require_POST
@apenas_administrador
def importar_lote_clientes(request):
    from clientes.lote_planilha import importar_planilha_clientes

    arquivo = request.FILES.get("planilha_clientes")
    if not arquivo:
        messages.error(request, "Selecione a planilha Excel (.xlsx) com os clientes do escritório.")
        return redirect("lista_empresas")
    if not (arquivo.name or "").lower().endswith(".xlsx"):
        messages.error(request, "Envie um arquivo no formato Excel (.xlsx).")
        return redirect("lista_empresas")

    resultado = importar_planilha_clientes(
        arquivo.read(),
        usuario=request.user,
    )
    criados = resultado.get("criados", 0)
    atualizados = resultado.get("atualizados", 0)
    erros = resultado.get("erros") or []

    if criados or atualizados:
        messages.success(
            request,
            f"Importação em lote concluída: {criados} cliente(s) criado(s) e {atualizados} atualizado(s).",
        )
    for err in erros[:15]:
        messages.error(request, err)
    if len(erros) > 15:
        messages.error(request, f"E mais {len(erros) - 15} linha(s) com aviso.")
    return redirect("lista_empresas")
