class CadastroIncompleto(ValueError):
    def __init__(self, mensagem: str, *, empresa_id: int | None = None, campos: list[str] | None = None):
        super().__init__(mensagem)
        self.empresa_id = empresa_id
        self.campos = list(campos or [])


def validar_cadastro_para_apuracao(empresa_obj):
    campos_faltantes = []
    if not empresa_obj.codigo_dominio:
        campos_faltantes.append("Código Domínio")
    if not empresa_obj.cnpj:
        campos_faltantes.append("CNPJ")
    if not empresa_obj.codigo_ibge:
        campos_faltantes.append("Código IBGE (Obrigatório para SPED e ISS)")
    if not empresa_obj.cnae_principal:
        campos_faltantes.append("CNAE Principal")
    if not empresa_obj.regime_tributario:
        campos_faltantes.append("Regime Tributário")
    if not (empresa_obj.inscricao_estadual or "").strip():
        campos_faltantes.append("Inscrição estadual")
    if empresa_obj.responsavel_apuracao_id is None:
        campos_faltantes.append("Responsável Operacional — Fiscal")
    if empresa_obj.responsavel_revisao_id is None:
        campos_faltantes.append("Responsável Revisador — Fiscal")

    if campos_faltantes:
        raise CadastroIncompleto(
            (
                f"APURAÇÃO BLOQUEADA: O cadastro da empresa {empresa_obj.razao_social} está incompleto. "
                f"Faltam os seguintes campos obrigatórios: {', '.join(campos_faltantes)}. "
                f"Atualize o cadastro antes de prosseguir."
            ),
            empresa_id=getattr(empresa_obj, "pk", None),
            campos=campos_faltantes,
        )
    return True


def avisar_cadastro_incompleto(request, erro: CadastroIncompleto) -> None:
    """Mostra o bloqueio e libera o botão «Atualizar cadastro» no layout."""
    from django.contrib import messages

    if erro.empresa_id:
        request.session["cadastro_bloqueio_empresa_id"] = erro.empresa_id
    messages.error(request, str(erro), extra_tags="cadastro_incompleto")
