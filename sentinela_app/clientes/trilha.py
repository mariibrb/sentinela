from clientes.models import Empresa, LogAlteracao

CAMPOS_REGIME = {
    "regime_tributario",
    "regime_pis_cofins",
    "anexo_simples",
    "perfil_estabelecimento",
    "possui_regime_especial",
}

CAMPOS_ESCOPO = {
    "contatos",
    "resumo_cliente",
    "escopo_fiscal",
    "qtd_notas_entrada",
    "qtd_notas_saida",
    "qtd_emissao_notas_servico",
    "qtd_emissao_notas_produto",
    "tipos_icms_incentivado",
    "estados_icms_st",
    "escopo_contabil",
    "escopo_dp",
    "escopo_regularizacao",
    "estados_st",
}


def texto_valor(valor):
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if valor is None:
        return ""
    nome = getattr(valor, "name", None)
    if nome is not None:
        return nome or ""
    return str(valor)


def foto_empresa(empresa):
    if empresa is None or not empresa.pk:
        return {}
    atual = Empresa.objects.get(pk=empresa.pk)
    return {
        campo.name: texto_valor(getattr(atual, campo.name))
        for campo in Empresa._meta.concrete_fields
        if campo.name not in {"id", "data_cadastro", "busca_escopo"}
    }


def registrar(usuario, empresa, acao, campo, anterior, novo):
    antes = texto_valor(anterior)
    depois = texto_valor(novo)
    if antes == depois:
        return None
    return LogAlteracao.objects.create(
        usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
        empresa=empresa,
        acao=acao,
        campo=campo[:80],
        valor_anterior=antes,
        valor_novo=depois,
    )


def registrar_empresa(usuario, antes, empresa):
    linhas = []
    for campo in Empresa._meta.concrete_fields:
        if campo.name in {"id", "data_cadastro", "busca_escopo"}:
            continue
        anterior = antes.get(campo.name, "")
        novo = texto_valor(getattr(empresa, campo.name))
        if anterior == novo:
            continue
        if campo.name in CAMPOS_REGIME:
            acao = "REGIME"
        elif campo.name in CAMPOS_ESCOPO:
            acao = "ESCOPO"
        else:
            acao = "CADASTRO"
        linhas.append(LogAlteracao(
            usuario=usuario if getattr(usuario, "is_authenticated", False) else None,
            empresa=empresa,
            acao=acao,
            campo=str(campo.verbose_name)[:80],
            valor_anterior=anterior,
            valor_novo=novo,
        ))
    if linhas:
        LogAlteracao.objects.bulk_create(linhas)
    return len(linhas)
