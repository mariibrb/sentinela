class CadastroIncompleto(ValueError):
    def __init__(self, mensagem: str, *, empresa_id: int | None = None, campos: list[str] | None = None):
        super().__init__(mensagem)
        self.empresa_id = empresa_id
        self.campos = list(campos or [])


VALOR_PROVISORIO_IBGE = "0000000"
VALOR_PROVISORIO_CNAE = "0000000"
VALOR_PROVISORIO_CEP = "00000000"
VALOR_PROVISORIO_TEXTO = "a definir"


def _texto(valor) -> str:
    return str(valor or "").strip()


def pendencias_cadastro(empresa_obj) -> list[str]:
    """Campos obrigatórios que faltam ou ainda têm o valor provisório da carga inicial."""
    pendencias = []
    if not empresa_obj.codigo_dominio:
        pendencias.append("Código Domínio")
    if not empresa_obj.cnpj:
        pendencias.append("CNPJ")

    ibge = _texto(empresa_obj.codigo_ibge)
    if not ibge:
        pendencias.append("Código IBGE (Obrigatório para SPED e ISS)")
    elif ibge == VALOR_PROVISORIO_IBGE:
        pendencias.append("Código IBGE válido (está com o provisório 0000000)")

    cnae = _texto(empresa_obj.cnae_principal)
    if not cnae:
        pendencias.append("CNAE Principal")
    elif cnae == VALOR_PROVISORIO_CNAE:
        pendencias.append("CNAE Principal válido (está com o provisório 0000000)")

    if not empresa_obj.regime_tributario:
        pendencias.append("Regime Tributário")
    if not _texto(empresa_obj.inscricao_estadual):
        pendencias.append("Inscrição estadual")
    if empresa_obj.responsavel_apuracao_id is None:
        pendencias.append("Responsável Operacional — Fiscal")
    if empresa_obj.responsavel_revisao_id is None:
        pendencias.append("Responsável Revisador — Fiscal")

    if _texto(empresa_obj.cep) == VALOR_PROVISORIO_CEP:
        pendencias.append("CEP válido (está com o provisório 00000000)")
    if _texto(empresa_obj.logradouro).casefold() == VALOR_PROVISORIO_TEXTO:
        pendencias.append("Logradouro (está como «A definir»)")
    if _texto(empresa_obj.municipio).casefold() == VALOR_PROVISORIO_TEXTO:
        pendencias.append("Município (está como «A definir»)")
    return pendencias


def validar_cadastro_para_apuracao(empresa_obj):
    campos_faltantes = pendencias_cadastro(empresa_obj)
    if campos_faltantes:
        raise CadastroIncompleto(
            (
                f"APURAÇÃO BLOQUEADA: O cadastro da empresa {empresa_obj.razao_social} está incompleto. "
                f"Faltam ou estão provisórios os seguintes campos obrigatórios: {', '.join(campos_faltantes)}. "
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
