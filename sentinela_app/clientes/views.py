from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, ProtectedError, Q
from django.http import JsonResponse
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
    notas_divergentes = []
    if empresa and competencia:
        notas_divergentes = DocumentoXML.objects.filter(empresa=empresa, competencia=competencia, possui_divergencia=True)
        try:
            validar_cadastro_para_apuracao(empresa)
            motor = MotorApuracaoSentinela(empresa, competencia)
            resumo_icms = motor.calcular_apuracao_icms()
            pis = motor.calcular_pis_cofins()
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
    empresas = Empresa.objects.annotate(notas=Count("documentos"))
    if termo:
        digitos = "".join(caractere for caractere in termo if caractere.isdigit())
        empresas = empresas.filter(
            Q(codigo_dominio__icontains=termo)
            | Q(razao_social__icontains=termo)
            | Q(nome_fantasia__icontains=termo)
            | Q(cnpj__icontains=digitos or termo)
        )
    return render(request, "clientes/lista.html", {"empresas": empresas, "termo": termo})


@login_required
def editar_empresa(request, empresa_id=None):
    request.modulo = "clientes"
    codigo = "pode_editar_cliente" if empresa_id else "pode_cadastrar_cliente"
    if not pode(request.user, codigo):
        messages.error(request, "Seu usuário não tem permissão para esta ação.")
        return redirect("lista_empresas")
    empresa = get_object_or_404(Empresa, pk=empresa_id) if empresa_id else None
    form = EmpresaForm(request.POST or None, request.FILES or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        antes = foto_empresa(empresa)
        empresa = form.save()
        registrar_empresa(request.user, antes, empresa)
        raiz = criar_pastas(empresa)
        messages.success(request, f"Cadastro gravado. Pastas criadas em {raiz}.")
        return redirect("lista_empresas")
    return render(request, "clientes/formulario.html", {"form": form, "empresa": empresa})


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
    empresa.delete()
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
    return JsonResponse({"campos": dados})


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
    return render(request, "colaboradores/formulario.html", {"form": form, "colaborador": colaborador})


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
    messages.success(request, f"Colaborador excluído: {nome}.")
    return redirect("lista_colaboradores")
