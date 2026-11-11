from __future__ import annotations

import json

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from clientes.acesso import pode
from clientes.competencia import interpretar, rotulo
from clientes.models import Colaborador, Empresa
from fechamento.definicoes import carregar_definicoes
from fechamento.models import ChecklistFechamento

User = get_user_model()


def _empresa_ativa(request):
    empresa_id = request.session.get("empresa_id")
    if not empresa_id:
        return None
    return Empresa.objects.filter(pk=empresa_id).first()


def _pode_fechamento(usuario):
    return pode(usuario, "pode_auditar") or pode(usuario, "pode_triar")


def _regime_sugerido(empresa: Empresa | None) -> str:
    if empresa is None:
        return "simples"
    rt = (empresa.regime_tributario or "").lower()
    if "mei" in rt:
        return "mei"
    if "presumido" in rt:
        return "presumido"
    if "real" in rt:
        return "real"
    if "simples" in rt:
        return "simples"
    return "simples"


def _estado_dict(registro: ChecklistFechamento | None, empresa: Empresa, competencia: str) -> dict:
    if registro is None:
        return {
            "empresa_id": empresa.pk,
            "competencia": competencia,
            "regime": _regime_sugerido(empresa),
            "checklist_tipo": "importacao",
            "status": "em_execucao",
            "flags": {},
            "obs_logs": {},
            "anexos": {},
            "operador": "",
            "revisor": "",
            "revisor_usuario_id": None,
            "ie_st": "nao_aplica",
            "aprovado_em": None,
            "header": {
                "name": f"{str(empresa.codigo_dominio).zfill(4)} - {empresa.razao_social}",
                "cnpj": empresa.cnpj,
            },
        }
    return {
        "empresa_id": empresa.pk,
        "competencia": registro.competencia,
        "regime": registro.regime,
        "checklist_tipo": registro.checklist_tipo,
        "status": registro.status,
        "flags": registro.flags or {},
        "obs_logs": registro.obs_logs or {},
        "anexos": registro.anexos or {},
        "operador": registro.operador,
        "revisor": registro.revisor,
        "revisor_usuario_id": registro.revisor_usuario_id,
        "ie_st": registro.ie_st,
        "aprovado_em": registro.aprovado_em.isoformat() if registro.aprovado_em else None,
        "header": {
            "name": f"{str(empresa.codigo_dominio).zfill(4)} - {empresa.razao_social}",
            "cnpj": empresa.cnpj,
        },
        "atualizado_em": registro.atualizado_em.isoformat(),
    }


@login_required
def painel_fechamento(request):
    request.modulo = "fechamento"
    if not _pode_fechamento(request.user):
        from django.contrib import messages
        from django.shortcuts import redirect

        messages.error(request, "Seu usuário não tem permissão para o Fechamento.")
        return redirect("painel")

    # Soft defaults only — Fechamento não depende da empresa/competência da apuração.
    competencia = request.session.get("competencia", "")
    partes = interpretar(competencia)
    definicoes = carregar_definicoes()
    return render(
        request,
        "fechamento/painel.html",
        {
            "competencia_sugerida": competencia if partes else "",
            "competencia_sugerida_rotulo": rotulo(competencia) if partes else "",
            "definicoes_json": json.dumps(definicoes, ensure_ascii=False),
        },
    )


@require_GET
@login_required
def api_empresas(request):
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)
    qs = Empresa.objects.filter(status="Ativo").order_by("codigo_dominio")
    empresas = [
        {
            "id": e.pk,
            "codigo_dominio": e.codigo_dominio,
            "razao_social": e.razao_social,
            "cnpj": e.cnpj,
            "regime_tributario": e.regime_tributario or "",
            "rotulo": f"{str(e.codigo_dominio).zfill(4)} - {e.razao_social}",
        }
        for e in qs
    ]
    return JsonResponse({"ok": True, "empresas": empresas, "empresa_ativa_id": request.session.get("empresa_id")})


@require_GET
@login_required
def api_lista(request):
    """Lista fechamentos de todos os clientes (visão revisador / central)."""
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)

    qs = ChecklistFechamento.objects.select_related("empresa", "revisor_usuario").order_by(
        "-atualizado_em", "empresa__codigo_dominio"
    )
    competencia = (request.GET.get("competencia") or "").strip()
    if competencia and interpretar(competencia) is not None:
        qs = qs.filter(competencia=competencia)
    status = (request.GET.get("status") or "").strip()
    if status and status in dict(ChecklistFechamento.STATUS):
        qs = qs.filter(status=status)

    itens = []
    for reg in qs[:2000]:
        emp = reg.empresa
        flags = reg.flags or {}
        feitos = sum(1 for v in flags.values() if v)
        itens.append(
            {
                "id": reg.pk,
                "codigo": str(reg.pk).zfill(6),
                "empresa_id": emp.pk,
                "cliente": f"{str(emp.codigo_dominio).zfill(4)} - {emp.razao_social}",
                "cnpj": emp.cnpj or "",
                "competencia": reg.competencia,
                "competencia_rotulo": rotulo(reg.competencia),
                "regime": reg.regime,
                "checklist_tipo": reg.checklist_tipo,
                "status": reg.status,
                "operador": reg.operador or "",
                "revisor": reg.revisor or "",
                "flags_marcadas": feitos,
                "atualizado_em": reg.atualizado_em.isoformat() if reg.atualizado_em else "",
            }
        )
    return JsonResponse(
        {
            "ok": True,
            "itens": itens,
            "competencia_sessao": request.session.get("competencia") or "",
            "total": len(itens),
        }
    )


@require_GET
@login_required
def api_revisores(request):
    """Lista usuários Sentinela que podem atuar como revisor (senha = login Sentinela)."""
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)
    revisores = []
    for colab in Colaborador.objects.filter(ativo=True, usuario__isnull=False).select_related("usuario").order_by("nome"):
        user = colab.usuario
        if user is None or not user.is_active:
            continue
        revisores.append(
            {
                "id": user.pk,
                "nome": colab.nome,
                "username": user.username,
                "rotulo": f"{colab.nome} ({user.username})",
            }
        )
    # Admins/staff sem colaborador também podem revisar
    for user in User.objects.filter(is_active=True, is_staff=True).order_by("username"):
        if any(r["id"] == user.pk for r in revisores):
            continue
        revisores.append(
            {
                "id": user.pk,
                "nome": user.get_full_name() or user.username,
                "username": user.username,
                "rotulo": f"{user.get_full_name() or user.username} ({user.username})",
            }
        )
    return JsonResponse({"ok": True, "revisores": revisores})


@require_GET
@login_required
def api_estado(request):
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)
    try:
        empresa_id = int(request.GET.get("empresa_id") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "erro": "empresa_id inválido."}, status=400)
    competencia = (request.GET.get("competencia") or request.session.get("competencia") or "").strip()
    if interpretar(competencia) is None:
        return JsonResponse({"ok": False, "erro": "Competência inválida."}, status=400)
    empresa = Empresa.objects.filter(pk=empresa_id, status="Ativo").first()
    if empresa is None:
        return JsonResponse({"ok": False, "erro": "Empresa não encontrada."}, status=404)

    registro = ChecklistFechamento.objects.filter(empresa=empresa, competencia=competencia).first()
    return JsonResponse(
        {
            "ok": True,
            "novo": registro is None,
            "estado": _estado_dict(registro, empresa, competencia),
        }
    )


@require_POST
@login_required
def api_salvar_estado(request):
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "erro": "JSON inválido."}, status=400)

    try:
        empresa_id = int(payload.get("empresa_id") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "erro": "empresa_id inválido."}, status=400)
    competencia = (payload.get("competencia") or request.session.get("competencia") or "").strip()
    if interpretar(competencia) is None:
        return JsonResponse({"ok": False, "erro": "Competência inválida."}, status=400)
    empresa = Empresa.objects.filter(pk=empresa_id, status="Ativo").first()
    if empresa is None:
        return JsonResponse({"ok": False, "erro": "Empresa não encontrada."}, status=404)

    regime = (payload.get("regime") or "simples").strip()
    if regime not in dict(ChecklistFechamento.REGIMES):
        regime = "simples"
    tipo = (payload.get("checklist_tipo") or "importacao").strip()
    if tipo not in dict(ChecklistFechamento.TIPOS):
        tipo = "importacao"

    status = (payload.get("status") or "em_execucao").strip()
    if status not in dict(ChecklistFechamento.STATUS):
        status = "em_execucao"
    # Aprovação só pela API dedicada (com senha do revisor).
    if status == "aprovado":
        return JsonResponse(
            {"ok": False, "erro": "Para aprovar, use o botão «Aprovar revisão» e digite a senha do revisador."},
            status=400,
        )

    revisor_usuario_id = payload.get("revisor_usuario_id")
    revisor_user = None
    if revisor_usuario_id:
        try:
            revisor_user = User.objects.filter(pk=int(revisor_usuario_id), is_active=True).first()
        except (TypeError, ValueError):
            revisor_user = None

    registro, _criado = ChecklistFechamento.objects.update_or_create(
        empresa=empresa,
        competencia=competencia,
        defaults={
            "regime": regime,
            "checklist_tipo": tipo,
            "status": status,
            "flags": payload.get("flags") or {},
            "obs_logs": payload.get("obs_logs") or {},
            "anexos": payload.get("anexos") or {},
            "operador": (payload.get("operador") or "")[:120],
            "revisor": (payload.get("revisor") or (revisor_user.get_username() if revisor_user else ""))[:120],
            "revisor_usuario": revisor_user,
            "ie_st": (payload.get("ie_st") or "nao_aplica")[:40],
        },
    )
    return JsonResponse(
        {
            "ok": True,
            "atualizado_em": registro.atualizado_em.isoformat(),
            "id": registro.pk,
            "status": registro.status,
        }
    )


@require_POST
@login_required
def api_aprovar(request):
    """Aprova o fechamento somente com a senha do usuário revisador do Sentinela."""
    if not _pode_fechamento(request.user):
        return JsonResponse({"ok": False, "erro": "Sem permissão."}, status=403)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "erro": "JSON inválido."}, status=400)

    try:
        empresa_id = int(payload.get("empresa_id") or 0)
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "erro": "empresa_id inválido."}, status=400)
    competencia = (payload.get("competencia") or request.session.get("competencia") or "").strip()
    if interpretar(competencia) is None:
        return JsonResponse({"ok": False, "erro": "Competência inválida."}, status=400)
    empresa = Empresa.objects.filter(pk=empresa_id, status="Ativo").first()
    if empresa is None:
        return JsonResponse({"ok": False, "erro": "Empresa não encontrada."}, status=404)

    registro = ChecklistFechamento.objects.filter(empresa=empresa, competencia=competencia).first()
    if registro is None:
        return JsonResponse({"ok": False, "erro": "Salve o checklist antes de aprovar."}, status=400)

    try:
        revisor_id = int(payload.get("revisor_usuario_id") or registro.revisor_usuario_id or 0)
    except (TypeError, ValueError):
        revisor_id = 0
    revisor = User.objects.filter(pk=revisor_id, is_active=True).first()
    if revisor is None:
        return JsonResponse({"ok": False, "erro": "Selecione o revisador (usuário do Sentinela)."}, status=400)

    senha = payload.get("senha_revisor") or ""
    if not senha:
        return JsonResponse({"ok": False, "erro": "Digite a senha do revisador."}, status=400)
    if not revisor.check_password(senha):
        return JsonResponse({"ok": False, "erro": "Senha do revisador incorreta."}, status=403)

    registro.status = "aprovado"
    registro.revisor_usuario = revisor
    registro.revisor = revisor.get_full_name() or revisor.username
    registro.aprovado_em = timezone.now()
    # Também persiste flags/obs enviados junto, se houver
    if isinstance(payload.get("flags"), dict):
        registro.flags = payload["flags"]
    if isinstance(payload.get("obs_logs"), dict):
        registro.obs_logs = payload["obs_logs"]
    registro.save()
    return JsonResponse(
        {
            "ok": True,
            "status": registro.status,
            "aprovado_em": registro.aprovado_em.isoformat(),
            "revisor": registro.revisor,
        }
    )
