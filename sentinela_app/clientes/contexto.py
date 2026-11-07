from django.utils import timezone

from clientes.acesso import administrador, mapa_permissoes
from clientes.competencia import MESES, interpretar, rotulo
from clientes.models import Empresa


def contexto_sentinela(request):
    empresas = []
    empresa = None
    usuario = getattr(request, "user", None)
    if usuario is not None and usuario.is_authenticated:
        empresas = list(Empresa.objects.all())
        empresa_id = request.session.get("empresa_id")
        empresa = next((item for item in empresas if item.pk == empresa_id), None)
        if empresa_id and empresa is None:
            request.session.pop("empresa_id", None)
    competencia = request.session.get("competencia") or ""
    partes = interpretar(competencia)
    if partes is None:
        hoje = timezone.localdate()
        partes = (hoje.month, hoje.year)
    return {
        "empresas_busca": empresas,
        "empresa_ativa": empresa,
        "competencia_ativa": competencia,
        "competencia_rotulo": rotulo(competencia) if interpretar(competencia) else rotulo(f"{partes[0]:02d}{partes[1]}"),
        "mes_ativo": partes[0],
        "ano_ativo": partes[1],
        "meses_nomes": MESES,
        "modulo": getattr(request, "modulo", "painel"),
        "eh_administrador": administrador(usuario),
        "permissoes": mapa_permissoes(usuario),
    }
