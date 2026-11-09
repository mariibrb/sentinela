"""Definições oficiais do Escopo de Trabalho e Onboarding do cliente no Sentinela.

Baseado nas diretrizes da Ficha de Novo Cliente da Nascel (onboarding):
- Contatos dos Responsáveis por setor
- Escopo: Compliance Fiscal (com quantidades médias, opções TTS e estados ICMS-ST)
- Escopo: Contabilidade
- Escopo: Departamento Pessoal
- Escopo: Regularização / Abertura (com estados para Inscrição ST)
- Resumo / Histórico do Cliente
- Consolidação textual para pesquisa rápida pela equipe.
"""

from __future__ import annotations

SETORES_CONTATO = (
    "Sócio/Diretor",
    "Financeiro",
    "Fiscal",
    "Contábil",
    "DP/RH",
    "Outro",
)

ESTADOS_BR = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)

OPCOES_TTS = (
    "TTS/CD DE INDÚSTRIA DE OUTRA UF",
    "TTS/CORREDOR DE IMPORTAÇÃO",
    "TTS/E-COMMERCE NÃO VINCULADO",
    "TTS/E-COMMERCE VINCULADO",
    "TTS/INDUSTRIA",
    "TTS/OPERADOR LOGÍSTICO",
)

COLUNA_1_FISCAL = (
    "Escrituração de Notas Fiscais de Entrada",
    "Escrituração de Notas Fiscais de Saída",
    "Escrituração de Notas de Serviços Prestados",
    "Escrituração de Notas de Serviços Tomados",
    "Emissão de notas fiscais de serviços",
    "Emissão de notas fiscais eletrônicas de produtos",
    "ICMS Incentivado",
)

COLUNA_2_FISCAL = (
    "Apuração: DAS",
    "Apuração: ICMS",
    "Apuração: ICMS-ST",
    "Apuração: ISS",
    "Apuração: PIS",
    "Apuração: COFINS",
)

COLUNA_3_FISCAL = (
    "Apuração: IRPJ",
    "Apuração: CSLL",
    "Apuração: IPI",
    "Apuração: IRRF",
    "Apuração: CSRF",
    "Apuração: INSS Retido",
)

COLUNA_4_FISCAL = (
    "Entrega: DCTFWeb",
    "Entrega: SPED Fiscal",
    "Entrega: SPED Contribuições",
    "Entrega: DEFIS",
    "Entrega: PGDAS-D",
    "Entrega: GIA / GIA-ST",
    "Entrega: SINTEGRA",
    "Consultas Tributárias",
    "Dúvidas Tributárias",
    "Acompanhamento de CND Federal",
    "Acompanhamento de CND Estadual",
    "Acompanhamento de CND Municipal",
    "Atendimento a fiscalizações e malhas fiscais",
    "Recálculo de Guias",
    "Recálculo de Parcelamentos",
    "Simulação de Parcelamentos",
)

ITENS_FISCAIS = COLUNA_1_FISCAL + COLUNA_2_FISCAL + COLUNA_3_FISCAL + COLUNA_4_FISCAL

ITENS_CONTABEIS = (
    "Processamento e Conciliação Bancária",
    "Elaboração de Balancetes Mensais/Trimestrais",
    "Balanço Patrimonial (BP)",
    "Demonstração do Resultado do Exercício (DRE)",
    "Demonstração dos Lucros ou Prejuízos Acumulados (DLPA)",
    "Demonstração das Mutações do Patrimônio Líquido (DMPL)",
    "Demonstração dos Fluxos de Caixa (DFC)",
    "Demonstração do Valor Adicionado (DVA)",
    "Elaboração de Notas Explicativas",
    "Entrega de SPED Contábil (ECD)",
    "Entrega de ECF",
    "Apuração de IRPJ e CSLL Lucro Real (Trimestral)",
    "Apuração de IRPJ e CSLL Lucro Real (Anual)",
)

ITENS_DP = (
    "Processamento de Folha de Pagamento Mensal",
    "Processamento de Adiantamento Salarial",
    "Processamento de Pró-labore",
    "Processamento de 13º Salário",
    "Gestão de Férias",
    "Gestão de Admissões",
    "Gestão de Rescisões",
    "Transmissão de eventos para o eSocial",
    "Entrega: EFD-Reinf",
    "Entrega: DCTFWeb",
    "Apuração de INSS (DARF Previdenciário)",
    "Apuração de FGTS",
    "Apuração de IRRF sobre folha",
    "Entrega de DIRF",
    "Emissão de Informe de Rendimentos",
    "Gestão de Parcelamentos",
    "Controle de Empréstimos Consignados",
)

ITENS_REGULARIZACAO = (
    "Elaboração de Contrato Social e Viabilidade",
    "Alteração Contratual",
    "Inscrição no CNPJ",
    "Inscrição Estadual",
    "Inscrição Municipal",
    "Obtenção de Alvará de Funcionamento",
    "Corpo de Bombeiros (AVCB/CLCB)",
    "Vigilância Sanitária",
    "IBAMA (CTF)",
    "Inscrições de Substituto Tributário (ST)",
)


def _marcados(mapa: dict | None, ordem: tuple[str, ...]) -> list[str]:
    if not isinstance(mapa, dict):
        return []
    return [item for item in ordem if bool(mapa.get(item))]


def montar_busca_escopo(empresa) -> str:
    """Consolida todos os itens marcados do escopo, contatos e observações para busca rápida."""
    partes: list[str] = []
    if getattr(empresa, "data_abertura", ""):
        partes.append(f"Abertura {empresa.data_abertura}")
    if getattr(empresa, "data_inicio_cliente", ""):
        partes.append(f"Cliente desde {empresa.data_inicio_cliente}")
    if getattr(empresa, "resumo_cliente", ""):
        partes.append(str(empresa.resumo_cliente))

    for c in getattr(empresa, "contatos", None) or []:
        if isinstance(c, dict):
            partes.append(
                " ".join(
                    str(c.get(k) or "").strip()
                    for k in ("Nome", "Setor", "E-mail", "Telefone")
                    if str(c.get(k) or "").strip()
                )
            )

    fisc = _marcados(getattr(empresa, "escopo_fiscal", {}), ITENS_FISCAIS)
    if fisc:
        partes.append("Compliance Fiscal: " + ", ".join(fisc))
    for attr, rot in (
        ("qtd_notas_entrada", "Qtd média Entradas"),
        ("qtd_notas_saida", "Qtd média Saídas"),
        ("qtd_emissao_notas_servico", "Qtd Emissão Serviços"),
        ("qtd_emissao_notas_produto", "Qtd Emissão Produtos"),
    ):
        val = str(getattr(empresa, attr, "") or "").strip()
        if val:
            partes.append(f"{rot}: {val}")

    tts = getattr(empresa, "tipos_icms_incentivado", None) or []
    if tts:
        partes.append("TTS ICMS Incentivado: " + ", ".join(str(x) for x in tts))

    uf_icms_st = getattr(empresa, "estados_icms_st", None) or []
    if uf_icms_st:
        partes.append("Estados ICMS-ST: " + ", ".join(str(x) for x in uf_icms_st))

    cont = _marcados(getattr(empresa, "escopo_contabil", {}), ITENS_CONTABEIS)
    if cont:
        partes.append("Contabilidade: " + ", ".join(cont))

    dp = _marcados(getattr(empresa, "escopo_dp", {}), ITENS_DP)
    if dp:
        partes.append("Departamento Pessoal DP: " + ", ".join(dp))

    reg = _marcados(getattr(empresa, "escopo_regularizacao", {}), ITENS_REGULARIZACAO)
    if reg:
        partes.append("Regularização Abertura: " + ", ".join(reg))

    uf_st = getattr(empresa, "estados_st", None) or []
    if uf_st:
        partes.append("Estados Inscrição ST: " + ", ".join(str(x) for x in uf_st))

    return " | ".join(p for p in partes if p)


def resumo_escopo_empresa(empresa) -> dict:
    """Retorna contagens e listas dos itens marcados do escopo para exibição na tela."""
    fisc = _marcados(getattr(empresa, "escopo_fiscal", {}), ITENS_FISCAIS)
    cont = _marcados(getattr(empresa, "escopo_contabil", {}), ITENS_CONTABEIS)
    dp = _marcados(getattr(empresa, "escopo_dp", {}), ITENS_DP)
    reg = _marcados(getattr(empresa, "escopo_regularizacao", {}), ITENS_REGULARIZACAO)
    macros = []
    if fisc:
        macros.append(f"Compliance Fiscal ({len(fisc)})")
    if cont:
        macros.append(f"Contabilidade ({len(cont)})")
    if dp:
        macros.append(f"DP ({len(dp)})")
    if reg:
        macros.append(f"Regularização ({len(reg)})")
    return {
        "fiscal": fisc,
        "contabil": cont,
        "dp": dp,
        "regularizacao": reg,
        "total_itens": len(fisc) + len(cont) + len(dp) + len(reg),
        "macros": macros,
    }
