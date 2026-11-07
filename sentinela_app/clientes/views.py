from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from auditoria.apuracao_motor import MotorApuracaoSentinela
from clientes.competencia import deslocar, montar
from clientes.forms import EmpresaForm
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML, Empresa
from clientes.pastas import criar_pastas
from clientes.services import ConsultaIndisponivel, ServicoConsultaCNPJ


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
    empresa = get_object_or_404(Empresa, pk=empresa_id) if empresa_id else None
    form = EmpresaForm(request.POST or None, request.FILES or None, instance=empresa)
    if request.method == "POST" and form.is_valid():
        empresa = form.save()
        raiz = criar_pastas(empresa)
        messages.success(request, f"Cadastro gravado. Pastas criadas em {raiz}.")
        return redirect("lista_empresas")
    return render(request, "clientes/formulario.html", {"form": form, "empresa": empresa})


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
