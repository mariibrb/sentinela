"""Planilha em TABELA ÚNICA (.xlsx) apenas com os dados manuais do escritório.

Ao subir a planilha, o Sentinela aciona automaticamente a consulta de CNPJ
(ServicoConsultaCNPJ.dados_cadastrais) para preencher Razão Social, Nome Fantasia,
Matriz/Filial, Data de Abertura, CNAE, CEP, Endereço, Município, UF, Código IBGE,
Inscrição Estadual principal e Inscrições de Substituto Tributário (0015).
"""
from __future__ import annotations

import io
import re
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from clientes.cnpj import digitos_conferem, somente_digitos
from clientes.escopo import (
    ESTADOS_BR,
    ITENS_CONTABEIS,
    ITENS_DP,
    ITENS_FISCAIS,
    ITENS_REGULARIZACAO,
    OPCOES_TTS,
    SETORES_CONTATO,
)
from clientes.models import Colaborador, Empresa, InscricaoSubstituto
from clientes.pastas import criar_pastas
from clientes.services import ServicoConsultaCNPJ
from clientes.trilha import foto_empresa, registrar, registrar_empresa

# Apenas os campos MANUAIS que o botão "Buscar CNPJ" não preenche sozinho
COLUNAS_MANUAIS: list[tuple[str, str, int, str]] = [
    ("codigo_dominio", "Código Domínio *", 18, "obrig"),
    ("cnpj", "CNPJ (14 dígitos) *", 22, "obrig"),
    ("regime_tributario", "Regime Tributário *", 24, "obrig"),
    ("anexo_simples", "Anexo Simples (I a V)", 20, "cad"),
    ("perfil_estabelecimento", "Perfil / Regime Especial", 26, "cad"),
    ("responsavel_apuracao", "Responsável Operacional Fiscal *", 28, "obrig"),
    ("responsavel_revisao", "Responsável Revisador Fiscal *", 28, "obrig"),
    ("responsavel_operacional_contabil", "Responsável Operacional Contábil", 28, "cad"),
    ("responsavel_revisador_contabil", "Responsável Revisador Contábil", 28, "cad"),
    ("responsavel_operacional_dp", "Responsável Operacional DP", 26, "cad"),
    ("responsavel_revisador_dp", "Responsável Revisador DP", 26, "cad"),
    ("data_inicio_cliente", "Cliente desde (MM/AAAA)", 22, "cad"),
    ("data_saida_cliente", "Data de Saída (se Inativo)", 24, "cad"),
    ("ramo_atividade", "Ramo de Atividade (Comércio/Indústria/Serviços)", 32, "cad"),
    ("inscricao_municipal", "Inscrição Municipal", 22, "cad"),
    ("suframa", "SUFRAMA", 16, "cad"),
    ("inscricao_estadual_manual", "IE Manual / ISENTO (se não vier na busca)", 30, "cad"),
    ("contribuinte_icms", "Contribuinte ICMS (Sim/Não)", 24, "cad"),
    ("contribuinte_ipi", "Contribuinte IPI (Sim/Não)", 24, "cad"),
    ("possui_regime_especial", "Regime Especial (Sim/Não)", 24, "cad"),
    ("contatos_texto", "Contatos (Nome | Setor | E-mail | Telefone | WhatsApp)", 50, "cad"),
    ("resumo_cliente", "Resumo / Histórico do Cliente", 40, "cad"),
    ("qtd_notas_entrada", "Qtd média Entradas / mês", 22, "escopo"),
    ("qtd_notas_saida", "Qtd média Saídas / mês", 22, "escopo"),
    ("qtd_emissao_notas_servico", "Qtd Emissão Serviços / mês", 22, "escopo"),
    ("qtd_emissao_notas_produto", "Qtd Emissão Produtos / mês", 22, "escopo"),
    ("tipos_icms_incentivado", "Incentivos TTS (separados por ;)", 34, "escopo"),
    ("estados_icms_st", "UFs Apuração ICMS-ST (ex: MG;SP)", 26, "escopo"),
    ("estados_st", "UFs Inscrição ST (ex: MG;SP;BA)", 26, "escopo"),
]

# Na mesma tabela única, logo à direita, entram os itens do Checklist de Escopo (Sim/Não)
COLUNAS_CHECKLIST_UNICAS: list[tuple[str, str, str]] = (
    [("Fiscal", item, f"[Departamento Fiscal] {item}") for item in ITENS_FISCAIS]
    + [("Contábil", item, f"[Departamento Contábil] {item}") for item in ITENS_CONTABEIS]
    + [("DP", item, f"[Departamento Pessoal] {item}") for item in ITENS_DP]
    + [("Regularização", item, f"[Regularização] {item}") for item in ITENS_REGULARIZACAO]
)

AJUDA_COLUNAS_MANUAIS: dict[str, str] = {
    "codigo_dominio": "Número da empresa no sistema Domínio (até 4 dígitos). Ex.: 0578.",
    "cnpj": "Informe os 14 dígitos do CNPJ. Ao subir a planilha, o Sentinela dispara sozinho o 'Buscar CNPJ' e traz Razão Social, Fantasia, Matriz/Filial, Abertura, CNAE, Endereço, IBGE, IE e IEs ST!",
    "regime_tributario": "Escolha na lista: Simples Nacional, Lucro Presumido, Lucro Real, MEI ou Imune/Isenta. O PIS/COFINS é preenchido sozinho pelo regime.",
    "anexo_simples": "Obrigatório quando Simples Nacional. Opções: I, II, III, IV ou V.",
    "perfil_estabelecimento": "Opções: GERAL, ECOMMERCE, INDUSTRIA, CD, REGIME_ESPECIAL_MG, REGIME_ESPECIAL_SP.",
    "responsavel_apuracao": "Nome do colaborador responsável pelo operacional do Departamento Fiscal (ex.: Ismael).",
    "responsavel_revisao": "Nome do colaborador responsável pela revisão do Departamento Fiscal (ex.: Mariana Barbosa).",
    "responsavel_operacional_contabil": "Nome do colaborador responsável pelo operacional do Departamento Contábil.",
    "responsavel_revisador_contabil": "Nome do colaborador responsável pela revisão do Departamento Contábil.",
    "responsavel_operacional_dp": "Nome do colaborador responsável pelo operacional do Departamento Pessoal (DP).",
    "responsavel_revisador_dp": "Nome do colaborador responsável pela revisão do Departamento Pessoal (DP).",
    "data_inicio_cliente": "Mês/Ano em que passou a ser cliente do escritório (MM/AAAA). Ex.: 01/2025.",
    "data_saida_cliente": "Obrigatório para clientes INATIVOS: data em que o cliente saiu (MM/AAAA ou DD/MM/AAAA) para sabermos a partir de quando cessam as obrigações nos setores.",
    "ramo_atividade": "Se deixar em branco, o Sentinela identifica sozinho pelo CNAE! Ou escolha: Comércio | Indústria | Serviços | Comércio e Indústria | Comércio e Serviços | Indústria e Serviços | Comércio, Indústria e Serviços | Holding / Patrimonial / Outros.",
    "inscricao_municipal": "Número da Inscrição Municipal (quando prestador de serviço). Ex.: 123456-0.",
    "suframa": "Inscrição SUFRAMA (quando houver). Ex.: 200123456.",
    "inscricao_estadual_manual": "Deixe em branco para usar a IE buscada automaticamente na Sefaz, ou escreva ISENTO (ou o nº manual).",
    "contribuinte_icms": "Escolha Sim ou Não.",
    "contribuinte_ipi": "Escolha Sim ou Não.",
    "possui_regime_especial": "Escolha Sim ou Não.",
    "contatos_texto": "Formato: Nome | Setor | E-mail | Telefone | WhatsApp(Sim/Não). Para mais de um contato, separe por ponto e vírgula (;).",
    "resumo_cliente": "Breve histórico, particularidades da operação ou observações importantes para a equipe.",
    "qtd_notas_entrada": "Média mensal de notas de entrada. Ex.: 80.",
    "qtd_notas_saida": "Média mensal de notas de saída. Ex.: 200.",
    "qtd_emissao_notas_servico": "Média mensal de emissão de NFS-e pelo escritório. Ex.: 15.",
    "qtd_emissao_notas_produto": "Média mensal de emissão de NF-e de produtos pelo escritório. Ex.: 40.",
    "tipos_icms_incentivado": "Opções válidas de TTS (separe por ; se tiver mais de uma):\n- " + "\n- ".join(OPCOES_TTS),
    "estados_icms_st": "Siglas das UFs onde possui apuração de ICMS-ST separadas por ; (ex.: MG; SP; BA).",
    "estados_st": "Siglas das UFs para controle de Inscrição de Substituto Tributário separadas por ; (ex.: SP; RJ; PR).",
}

CORES_CHECKLIST = {
    "Fiscal": {
        "cab": PatternFill("solid", fgColor="1D4ED8"),     # Azul forte
        "cel": PatternFill("solid", fgColor="EFF6FF"),     # Azul clarinho
    },
    "Contábil": {
        "cab": PatternFill("solid", fgColor="047857"),     # Esmeralda forte
        "cel": PatternFill("solid", fgColor="ECFDF5"),     # Esmeralda clarinho
    },
    "DP": {
        "cab": PatternFill("solid", fgColor="6D28D9"),     # Violeta forte
        "cel": PatternFill("solid", fgColor="F5F3FF"),     # Violeta clarinho
    },
    "Regularização": {
        "cab": PatternFill("solid", fgColor="B45309"),     # Âmbar forte
        "cel": PatternFill("solid", fgColor="FFFBEB"),     # Âmbar clarinho
    },
}


def _sim_nao(valor: bool) -> str:
    return "Sim" if valor else "Não"


def _interpretar_booleano(valor: Any, padrao: bool = False) -> bool:
    if valor is None:
        return padrao
    texto = str(valor).strip().lower()
    if not texto:
        return padrao
    return texto in {"sim", "s", "x", "1", "true", "verdadeiro", "yes", "ativo", "ok"}


def _normalizar_cabecalho(texto: Any) -> str:
    bruto = str(texto or "").strip().lower()
    bruto = re.sub(r"\(\?\)", "", bruto)
    bruto = re.sub(r"\*+", "", bruto).strip()
    return bruto


def _formatar_contatos_planilha(contatos: list[dict] | None) -> str:
    if not isinstance(contatos, list) or not contatos:
        return ""
    linhas = []
    for c in contatos:
        if not isinstance(c, dict):
            continue
        nome = str(c.get("Nome") or "").strip()
        setor = str(c.get("Setor") or "Outro").strip()
        email = str(c.get("E-mail") or "").strip()
        tel = str(c.get("Telefone") or "").strip()
        zap = "Sim" if c.get("WhatsApp") else "Não"
        if any((nome, email, tel)):
            linhas.append(f"{nome} | {setor} | {email} | {tel} | {zap}")
    return " ; ".join(linhas)


def _interpretar_contatos_planilha(texto: str) -> list[dict]:
    if not texto or not str(texto).strip():
        return []
    bruto = str(texto).strip()
    partes = [p.strip() for p in re.split(r"[;\n]+", bruto) if p.strip()]
    contatos = []
    for item in partes:
        campos = [c.strip() for c in item.split("|")]
        nome = campos[0] if len(campos) > 0 else ""
        setor = campos[1] if len(campos) > 1 and campos[1] else "Outro"
        email = campos[2] if len(campos) > 2 else ""
        telefone = campos[3] if len(campos) > 3 else ""
        zap = _interpretar_booleano(campos[4], True) if len(campos) > 4 else True
        if any((nome, email, telefone)):
            contatos.append({
                "Nome": nome,
                "Setor": setor if setor in SETORES_CONTATO else "Outro",
                "E-mail": email,
                "Telefone": telefone,
                "WhatsApp": zap,
            })
    return contatos


def gerar_planilha_modelo_clientes(incluir_existentes: bool = True) -> bytes:
    """Gera a planilha Excel (.xlsx) em TABELA ÚNICA apenas com os campos manuais (que a busca de CNPJ não traz)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Clientes_Escritorio"

    fonte_titulo = Font(name="Segoe UI", size=12, bold=True, color="FFFFFF")
    fonte_cab_obrig = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    fonte_cab_cad = Font(name="Segoe UI", size=10, bold=True, color="1F2937")
    fonte_cab_esc = Font(name="Segoe UI", size=9, bold=True, color="FFFFFF")
    fonte_celula = Font(name="Segoe UI", size=10, color="111827")
    fonte_dica = Font(name="Segoe UI", size=9, italic=True, color="4B5563")

    preench_titulo = PatternFill("solid", fgColor="9D174D")  # Pink 800
    preench_obrig = PatternFill("solid", fgColor="DB2777")   # Pink 600
    preench_cad = PatternFill("solid", fgColor="FCE7F3")     # Pink 100
    preench_esc = PatternFill("solid", fgColor="0F766E")     # Teal 700 (Escopo)
    preench_dica = PatternFill("solid", fgColor="F5F5F4")
    preench_zebra = PatternFill("solid", fgColor="FFF1F2")

    borda_fina = Border(
        left=Side(style="thin", color="D6D3D1"),
        right=Side(style="thin", color="D6D3D1"),
        top=Side(style="thin", color="D6D3D1"),
        bottom=Side(style="thin", color="D6D3D1"),
    )

    total_cols = len(COLUNAS_MANUAIS) + len(COLUNAS_CHECKLIST_UNICAS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(total_cols, 28))
    cel_titulo = ws.cell(
        row=1,
        column=1,
        value=(
            "SENTINELA NASCEL — PLANILHA EM LOTE APENAS DE DADOS MANUAIS "
            "(Razão Social, Fantasia, Matriz/Filial, Abertura, CNAE, Endereço, IBGE, IE e IEs ST vêm automático pelo CNPJ!)"
        ),
    )
    cel_titulo.font = fonte_titulo
    cel_titulo.fill = preench_titulo
    cel_titulo.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=min(total_cols, 28))
    cel_dica = ws.cell(
        row=2,
        column=1,
        value=(
            "Ao subir esta planilha, o Sentinela executa a busca automática de cada CNPJ (igual ao botão 'Buscar CNPJ') "
            "e junta com os dados manuais abaixo (Código Domínio, Regime Tributário, Responsáveis, Contatos e Checklist de Escopo)."
        ),
    )
    cel_dica.font = fonte_dica
    cel_dica.fill = preench_dica
    cel_dica.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[2].height = 22

    # Linha 3: Cabeçalhos da Tabela Única (somente dados manuais + checklist colorido por departamento)
    ws.row_dimensions[3].height = 36
    for idx, (chave, titulo, largura, estilo) in enumerate(COLUNAS_MANUAIS, start=1):
        rotulo_com_ajuda = f"{titulo} (?)" if chave in AJUDA_COLUNAS_MANUAIS else titulo
        c = ws.cell(row=3, column=idx, value=rotulo_com_ajuda)
        if estilo == "obrig":
            c.font = fonte_cab_obrig
            c.fill = preench_obrig
        elif estilo == "escopo":
            c.font = fonte_cab_esc
            c.fill = preench_esc
        else:
            c.font = fonte_cab_cad
            c.fill = preench_cad
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = borda_fina
        ws.column_dimensions[get_column_letter(idx)].width = largura + 3
        texto_ajuda = AJUDA_COLUNAS_MANUAIS.get(chave)
        if texto_ajuda:
            comentario = Comment(f"❓ COMO PREENCHER:\n{texto_ajuda}", "Sentinela")
            comentario.width = 320
            comentario.height = 130
            c.comment = comentario

    offset_check = len(COLUNAS_MANUAIS)
    for idx_c, (grupo, item_nome, rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_check + 1):
        cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
        c = ws.cell(row=3, column=idx_c, value=f"{rotulo_col} (?)")
        c.font = fonte_cab_esc
        c.fill = cor_grupo["cab"]
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = borda_fina
        ws.column_dimensions[get_column_letter(idx_c)].width = 25
        c.comment = Comment(
            f"❓ CHECKLIST {grupo.upper()}:\nMarque 'Sim' (ou 'X') se o serviço '{item_nome}' faz parte do escopo contratado por este cliente.",
            "Sentinela",
        )

    empresas_existentes = list(
        Empresa.objects.select_related(
            "responsavel_apuracao",
            "responsavel_revisao",
            "responsavel_operacional_contabil",
            "responsavel_revisador_contabil",
            "responsavel_operacional_dp",
            "responsavel_revisador_dp",
        ).order_by("codigo_dominio")
    ) if incluir_existentes else []

    linha_atual = 4
    if empresas_existentes:
        for emp in empresas_existentes:
            valores = {
                "codigo_dominio": emp.codigo_dominio,
                "cnpj": emp.cnpj,
                "regime_tributario": emp.regime_tributario,
                "anexo_simples": emp.anexo_simples or "",
                "perfil_estabelecimento": emp.perfil_estabelecimento,
                "responsavel_apuracao": emp.responsavel_apuracao.nome if emp.responsavel_apuracao else "",
                "responsavel_revisao": emp.responsavel_revisao.nome if emp.responsavel_revisao else "",
                "responsavel_operacional_contabil": emp.responsavel_operacional_contabil.nome if emp.responsavel_operacional_contabil else "",
                "responsavel_revisador_contabil": emp.responsavel_revisador_contabil.nome if emp.responsavel_revisador_contabil else "",
                "responsavel_operacional_dp": emp.responsavel_operacional_dp.nome if emp.responsavel_operacional_dp else "",
                "responsavel_revisador_dp": emp.responsavel_revisador_dp.nome if emp.responsavel_revisador_dp else "",
                "data_inicio_cliente": emp.data_inicio_cliente or "",
                "data_saida_cliente": emp.data_saida_cliente or "",
                "ramo_atividade": emp.ramo_atividade or "",
                "inscricao_municipal": emp.inscricao_municipal or "",
                "suframa": emp.suframa or "",
                "inscricao_estadual_manual": "ISENTO" if (emp.inscricao_estadual or "").upper() == "ISENTO" else "",
                "contribuinte_icms": _sim_nao(emp.contribuinte_icms),
                "contribuinte_ipi": _sim_nao(emp.contribuinte_ipi),
                "possui_regime_especial": _sim_nao(emp.possui_regime_especial),
                "contatos_texto": _formatar_contatos_planilha(emp.contatos),
                "resumo_cliente": emp.resumo_cliente or "",
                "qtd_notas_entrada": emp.qtd_notas_entrada or "",
                "qtd_notas_saida": emp.qtd_notas_saida or "",
                "qtd_emissao_notas_servico": emp.qtd_emissao_notas_servico or "",
                "qtd_emissao_notas_produto": emp.qtd_emissao_notas_produto or "",
                "tipos_icms_incentivado": "; ".join(emp.tipos_icms_incentivado or []),
                "estados_icms_st": "; ".join(emp.estados_icms_st or []),
                "estados_st": "; ".join(emp.estados_st or []),
            }
            for col_idx, (chave, _titulo, _larg, _est) in enumerate(COLUNAS_MANUAIS, start=1):
                cel = ws.cell(row=linha_atual, column=col_idx, value=valores.get(chave, ""))
                cel.font = fonte_celula
                cel.border = borda_fina
                cel.number_format = "@"

            for idx_c, (grupo, item_nome, _rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_check + 1):
                cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
                mapa = (
                    emp.escopo_fiscal if grupo == "Fiscal"
                    else emp.escopo_contabil if grupo == "Contábil"
                    else emp.escopo_dp if grupo == "DP"
                    else emp.escopo_regularizacao
                ) or {}
                cel_chk = ws.cell(row=linha_atual, column=idx_c, value="Sim" if mapa.get(item_nome) else "")
                cel_chk.font = fonte_celula
                cel_chk.fill = cor_grupo["cel"]
                cel_chk.alignment = Alignment(horizontal="center")
                cel_chk.border = borda_fina
            linha_atual += 1
    else:
        # Primeira linha 100% preenchida em TODAS as colunas para servir de exemplo visual claro
        exemplo = {
            "codigo_dominio": "0578",
            "cnpj": "45785442000390",
            "regime_tributario": "Lucro Presumido",
            "anexo_simples": "I",
            "perfil_estabelecimento": "GERAL",
            "responsavel_apuracao": "Ismael",
            "responsavel_revisao": "Mariana Barbosa",
            "responsavel_operacional_contabil": "Ismael",
            "responsavel_revisador_contabil": "Mariana Barbosa",
            "responsavel_operacional_dp": "Ismael",
            "responsavel_revisador_dp": "Mariana Barbosa",
            "data_inicio_cliente": "01/2025",
            "data_saida_cliente": "12/2026",
            "ramo_atividade": "Comércio e Indústria",
            "inscricao_municipal": "123456",
            "suframa": "200123456",
            "inscricao_estadual_manual": "0047805660026",
            "contribuinte_icms": "Sim",
            "contribuinte_ipi": "Não",
            "possui_regime_especial": "Não",
            "contatos_texto": "Financeiro Cliente | Fiscal | fiscal@exemplo.com.br | (35) 99999-0000 | Sim",
            "resumo_cliente": "Linha de exemplo com todas as colunas preenchidas (substitua ou adicione abaixo os clientes).",
            "qtd_notas_entrada": "80",
            "qtd_notas_saida": "200",
            "qtd_emissao_notas_servico": "10",
            "qtd_emissao_notas_produto": "150",
            "tipos_icms_incentivado": "TTS/CORREDOR DE IMPORTAÇÃO; TTS/INDUSTRIA",
            "estados_icms_st": "CE; SP; BA",
            "estados_st": "CE; SP; BA",
        }
        for col_idx, (chave, _titulo, _larg, _est) in enumerate(COLUNAS_MANUAIS, start=1):
            cel = ws.cell(row=linha_atual, column=col_idx, value=exemplo.get(chave, ""))
            cel.font = fonte_celula
            cel.fill = preench_zebra
            cel.border = borda_fina
            cel.number_format = "@"
        for idx_c, (grupo, _item_nome, _rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_check + 1):
            cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
            cel_chk = ws.cell(row=linha_atual, column=idx_c, value="Sim")
            cel_chk.font = fonte_celula
            cel_chk.fill = cor_grupo["cel"]
            cel_chk.alignment = Alignment(horizontal="center")
            cel_chk.border = borda_fina
        linha_atual += 1

    # Pinta também as células em branco do checklist com a cor suave de cada departamento (Fiscal/Contábil/DP/Regularização)
    for r in range(linha_atual, 120):
        for c_idx in (1, 2, 12, 13, 14, 15):
            ws.cell(row=r, column=c_idx).number_format = "@"
        for idx_c, (grupo, _item_nome, _rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_check + 1):
            cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
            cel_vazia = ws.cell(row=r, column=idx_c)
            cel_vazia.fill = cor_grupo["cel"]
            cel_vazia.border = borda_fina
            cel_vazia.alignment = Alignment(horizontal="center")

    ws.freeze_panes = "C4"
    ws.auto_filter.ref = f"A3:{get_column_letter(total_cols)}{max(linha_atual, 50)}"

    def _adicionar_validacao(col_idx: int, opcoes_lista: list[str]) -> None:
        formula = '"' + ",".join(opcoes_lista) + '"'
        if len(formula) > 250:
            return
        dv = DataValidation(type="list", formula1=formula, allow_blank=True)
        letra = get_column_letter(col_idx)
        dv.add(f"{letra}4:{letra}300")
        ws.add_data_validation(dv)

    _adicionar_validacao(3, [r[0] for r in Empresa.REGIMES_TRIBUTARIOS])
    _adicionar_validacao(4, ["I", "II", "III", "IV", "V"])
    _adicionar_validacao(5, [p[0] for p in Empresa.PERFIS_ESTABELECIMENTO])
    _adicionar_validacao(14, [r[0] for r in Empresa.RAMOS_ATIVIDADE])
    _adicionar_validacao(18, ["Sim", "Não"])
    _adicionar_validacao(19, ["Sim", "Não"])
    _adicionar_validacao(20, ["Sim", "Não"])

    nomes_colaboradores = list(Colaborador.objects.filter(ativo=True).order_by("nome").values_list("nome", flat=True))
    if nomes_colaboradores:
        for col_resp in (6, 7, 8, 9, 10, 11):
            _adicionar_validacao(col_resp, nomes_colaboradores)

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _resolver_colaborador(valor: str) -> Colaborador | None:
    limpo = (valor or "").strip()
    if not limpo or limpo.lower() in {"definir depois", "-", "nenhum"}:
        return None
    return (
        Colaborador.objects.filter(nome__iexact=limpo).first()
        or Colaborador.objects.filter(email__iexact=limpo).first()
        or Colaborador.objects.filter(nome__icontains=limpo).first()
    )


def _normalizar_regime(valor: str) -> str:
    limpo = (valor or "").strip()
    mapa = {
        "simples": "Simples Nacional",
        "simples nacional": "Simples Nacional",
        "lucro presumido": "Lucro Presumido",
        "presumido": "Lucro Presumido",
        "lucro real": "Lucro Real",
        "real": "Lucro Real",
        "mei": "MEI",
        "imune": "Imune/Isenta",
        "isenta": "Imune/Isenta",
        "imune/isenta": "Imune/Isenta",
    }
    return mapa.get(limpo.lower(), limpo)


def _normalizar_perfil(valor: str) -> str:
    limpo = (valor or "").strip().upper()
    validos = dict(Empresa.PERFIS_ESTABELECIMENTO)
    if limpo in validos:
        return limpo
    for codigo, rotulo in Empresa.PERFIS_ESTABELECIMENTO:
        if limpo and (limpo == rotulo.upper() or limpo in rotulo.upper()):
            return codigo
    return "GERAL"


def importar_planilha_clientes(
    arquivo_bytes: bytes,
    *,
    usuario=None,
) -> dict[str, Any]:
    """Lê a tabela única de dados manuais (.xlsx), busca automaticamente cada CNPJ e grava os clientes."""
    try:
        wb = load_workbook(io.BytesIO(arquivo_bytes), data_only=True)
    except Exception as exc:
        return {
            "ok": False,
            "criados": 0,
            "atualizados": 0,
            "erros": [f"Não foi possível abrir a planilha Excel (.xlsx): {exc}"],
        }

    ws = wb.worksheets[0]
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        return {"ok": False, "criados": 0, "atualizados": 0, "erros": ["A planilha está vazia."]}

    idx_cabecalho = None
    for idx, row in enumerate(linhas[:15]):
        textos = [_normalizar_cabecalho(c) for c in (row or [])]
        nao_vazios = [t for t in textos if t]
        if len(nao_vazios) >= 3 and any("cnpj" in t for t in textos) and any("domínio" in t or "dominio" in t for t in textos):
            idx_cabecalho = idx
            break
    if idx_cabecalho is None:
        return {
            "ok": False,
            "criados": 0,
            "atualizados": 0,
            "erros": ["Cabeçalho não encontrado. Baixe a Planilha Modelo (.xlsx) na tela de Clientes e preencha os dados nela."],
        }

    cabecalhos_brutos = [str(c or "").strip() for c in linhas[idx_cabecalho]]
    cabecalhos_norm = [_normalizar_cabecalho(c) for c in cabecalhos_brutos]

    mapa_aliases: dict[str, str] = {}
    for chave, titulo, _larg, _est in COLUNAS_MANUAIS:
        mapa_aliases[_normalizar_cabecalho(titulo)] = chave
        mapa_aliases[chave.lower()] = chave

    aliases_extras = {
        "código domínio": "codigo_dominio",
        "codigo dominio": "codigo_dominio",
        "código": "codigo_dominio",
        "codigo": "codigo_dominio",
        "cnpj": "cnpj",
        "regime": "regime_tributario",
        "regime tributário": "regime_tributario",
        "regime tributario": "regime_tributario",
        "anexo": "anexo_simples",
        "anexo simples": "anexo_simples",
        "perfil": "perfil_estabelecimento",
        "responsável apuração": "responsavel_apuracao",
        "responsavel apuracao": "responsavel_apuracao",
        "responsável operacional fiscal": "responsavel_apuracao",
        "responsavel operacional fiscal": "responsavel_apuracao",
        "operacional fiscal": "responsavel_apuracao",
        "responsável revisão": "responsavel_revisao",
        "responsavel revisao": "responsavel_revisao",
        "responsável revisador fiscal": "responsavel_revisao",
        "responsavel revisador fiscal": "responsavel_revisao",
        "revisador fiscal": "responsavel_revisao",
        "responsável operacional contábil": "responsavel_operacional_contabil",
        "responsavel operacional contabil": "responsavel_operacional_contabil",
        "operacional contábil": "responsavel_operacional_contabil",
        "operacional contabil": "responsavel_operacional_contabil",
        "responsável revisador contábil": "responsavel_revisador_contabil",
        "responsavel revisador contabil": "responsavel_revisador_contabil",
        "revisador contábil": "responsavel_revisador_contabil",
        "revisador contabil": "responsavel_revisador_contabil",
        "responsável operacional dp": "responsavel_operacional_dp",
        "responsavel operacional dp": "responsavel_operacional_dp",
        "operacional dp": "responsavel_operacional_dp",
        "responsável revisador dp": "responsavel_revisador_dp",
        "responsavel revisador dp": "responsavel_revisador_dp",
        "revisador dp": "responsavel_revisador_dp",
        "cliente desde": "data_inicio_cliente",
        "im": "inscricao_municipal",
        "inscrição municipal": "inscricao_municipal",
        "inscricao municipal": "inscricao_municipal",
        "ie": "inscricao_estadual_manual",
        "inscrição estadual": "inscricao_estadual_manual",
        "inscricao estadual": "inscricao_estadual_manual",
        "ie manual": "inscricao_estadual_manual",
    }
    mapa_aliases.update(aliases_extras)

    col_para_chave: dict[int, str] = {}
    chaves_ja_mapeadas: set[str] = set()
    cols_checklist: dict[int, tuple[str, str]] = {}

    for c_idx, nome_cab in enumerate(cabecalhos_norm):
        if not nome_cab:
            continue
        bruto_col = re.sub(r"\s*\(\?\)\s*$", "", cabecalhos_brutos[c_idx]).strip()
        m_chk = re.match(
            r"^\[(?:Departamento\s+)?(Fiscal|Contábil|Contabil|Pessoal|DP|Regularização|Regularizacao)\]\s*(.+)$",
            bruto_col,
            flags=re.I,
        )
        if m_chk:
            grp_raw = m_chk.group(1).lower()
            grp = (
                "Fiscal" if grp_raw.startswith("fisc")
                else "Contábil" if grp_raw.startswith("cont")
                else "DP" if grp_raw in {"dp", "pessoal"}
                else "Regularização"
            )
            cols_checklist[c_idx] = (grp, m_chk.group(2).strip())
            continue
        if nome_cab in mapa_aliases:
            alvo = mapa_aliases[nome_cab]
            col_para_chave[c_idx] = alvo
            chaves_ja_mapeadas.add(alvo)
            continue
        for alias, alvo in mapa_aliases.items():
            if alias and alias in nome_cab and c_idx not in col_para_chave and alvo not in chaves_ja_mapeadas:
                col_para_chave[c_idx] = alvo
                chaves_ja_mapeadas.add(alvo)
                break

    criados = 0
    atualizados = 0
    erros: list[str] = []

    for offset_linha, row in enumerate(linhas[idx_cabecalho + 1:], start=idx_cabecalho + 2):
        if not row or not any(str(c or "").strip() for c in row):
            continue

        dados_linha: dict[str, str] = {}
        for c_idx, chave in col_para_chave.items():
            if c_idx < len(row) and row[c_idx] is not None:
                dados_linha[chave] = str(row[c_idx]).strip()
            else:
                dados_linha[chave] = ""

        codigo_dominio = somente_digitos(dados_linha.get("codigo_dominio", ""))
        cnpj = somente_digitos(dados_linha.get("cnpj", ""))
        if cnpj and len(cnpj) < 14:
            cnpj = cnpj.zfill(14)

        if not codigo_dominio and not cnpj:
            continue

        if not codigo_dominio or len(codigo_dominio) > 4:
            erros.append(f"Linha {offset_linha}: Código Domínio inválido ('{dados_linha.get('codigo_dominio', '')}'). Informe até 4 dígitos.")
            continue

        if len(cnpj) != 14 or not digitos_conferem(cnpj):
            erros.append(f"Linha {offset_linha} (Domínio {codigo_dominio}): CNPJ inválido ('{dados_linha.get('cnpj', '')}').")
            continue

        regime_tributario = _normalizar_regime(dados_linha.get("regime_tributario", ""))
        if regime_tributario not in dict(Empresa.REGIMES_TRIBUTARIOS):
            erros.append(
                f"Linha {offset_linha} (Domínio {codigo_dominio}): Regime Tributário '{dados_linha.get('regime_tributario', '')}' inválido. "
                "Use Simples Nacional, Lucro Presumido, Lucro Real, MEI ou Imune/Isenta."
            )
            continue

        anexo_simples = (dados_linha.get("anexo_simples") or "").strip().upper().replace("ANEXO", "").strip()
        if regime_tributario == "Simples Nacional" and anexo_simples not in dict(Empresa.ANEXOS_SIMPLES):
            anexo_simples = "I"

        empresa = (
            Empresa.objects.filter(codigo_dominio=codigo_dominio).first()
            or Empresa.objects.filter(cnpj=cnpj).first()
        )
        eh_novo = empresa is None
        antes = foto_empresa(empresa) if empresa else {}

        # Aciona automaticamente a mesma busca do botão "Buscar CNPJ" (ServicoConsultaCNPJ.dados_cadastrais)
        dados_api: dict[str, Any] = {}
        substituicoes_api: dict[str, str] = {}
        try:
            dados_api = ServicoConsultaCNPJ.dados_cadastrais(cnpj)
            substituicoes_api = dados_api.pop("substituicoes", {}) or {}
        except Exception as exc_api:
            if eh_novo:
                erros.append(
                    f"Linha {offset_linha} (Domínio {codigo_dominio} · CNPJ {cnpj}): "
                    f"a busca automática do CNPJ não respondeu ({exc_api}). Tente subir novamente em instantes."
                )
                continue

        if eh_novo:
            empresa = Empresa(codigo_dominio=codigo_dominio, cnpj=cnpj)
        else:
            conflito_cod = Empresa.objects.filter(codigo_dominio=codigo_dominio).exclude(pk=empresa.pk).first()
            conflito_cnpj = Empresa.objects.filter(cnpj=cnpj).exclude(pk=empresa.pk).first()
            if conflito_cod or conflito_cnpj:
                erros.append(
                    f"Linha {offset_linha}: conflito de Código Domínio ({codigo_dominio}) ou CNPJ ({cnpj}) com outro cliente já cadastrado."
                )
                continue
            empresa.codigo_dominio = codigo_dominio
            empresa.cnpj = cnpj

        # Preenche tudo que vem automático do botão "Buscar CNPJ"
        if dados_api.get("razao_social"):
            empresa.razao_social = str(dados_api["razao_social"])[:255]
        if dados_api.get("nome_fantasia"):
            empresa.nome_fantasia = str(dados_api["nome_fantasia"])[:255]
        if dados_api.get("tipo_estabelecimento"):
            empresa.tipo_estabelecimento = str(dados_api["tipo_estabelecimento"])[:10]
        if dados_api.get("status"):
            empresa.status = str(dados_api["status"])[:20]
        if dados_api.get("data_abertura"):
            empresa.data_abertura = str(dados_api["data_abertura"])[:20]
        if dados_api.get("cep"):
            empresa.cep = somente_digitos(str(dados_api["cep"]))[:10]
        if dados_api.get("uf"):
            empresa.uf = str(dados_api["uf"]).strip().upper()[:2]
        if dados_api.get("municipio"):
            empresa.municipio = str(dados_api["municipio"])[:100]
        if dados_api.get("codigo_ibge"):
            empresa.codigo_ibge = somente_digitos(str(dados_api["codigo_ibge"]))[:7]
        if dados_api.get("logradouro"):
            empresa.logradouro = str(dados_api["logradouro"])[:255]
        if dados_api.get("numero"):
            empresa.numero = str(dados_api["numero"])[:20]
        elif not empresa.numero:
            empresa.numero = "S/N"
        if dados_api.get("complemento") is not None:
            empresa.complemento = str(dados_api.get("complemento") or "")[:100]
        if dados_api.get("bairro"):
            empresa.bairro = str(dados_api["bairro"])[:100]
        if dados_api.get("cnae_principal"):
            empresa.cnae_principal = somente_digitos(str(dados_api["cnae_principal"]))[:10]
        if dados_api.get("cnae_descricao"):
            empresa.cnae_descricao = str(dados_api["cnae_descricao"])[:255]
        if dados_api.get("ramo_atividade") and not (dados_linha.get("ramo_atividade") or "").strip():
            empresa.ramo_atividade = str(dados_api["ramo_atividade"])[:50]
        if dados_api.get("inscricao_estadual"):
            empresa.inscricao_estadual = str(dados_api["inscricao_estadual"])[:30]

        # Preenche os dados MANUAIS vindos da planilha
        empresa.regime_tributario = regime_tributario
        empresa.anexo_simples = anexo_simples if regime_tributario == "Simples Nacional" else None
        empresa.perfil_estabelecimento = _normalizar_perfil(dados_linha.get("perfil_estabelecimento", ""))
        ramo_manual = (dados_linha.get("ramo_atividade") or "").strip()
        if ramo_manual in dict(Empresa.RAMOS_ATIVIDADE):
            empresa.ramo_atividade = ramo_manual

        for campo_resp in (
            "responsavel_apuracao",
            "responsavel_revisao",
            "responsavel_operacional_contabil",
            "responsavel_revisador_contabil",
            "responsavel_operacional_dp",
            "responsavel_revisador_dp",
        ):
            col_resolvido = _resolver_colaborador(dados_linha.get(campo_resp, ""))
            if col_resolvido is not None:
                setattr(empresa, campo_resp, col_resolvido)

        ie_manual = (dados_linha.get("inscricao_estadual_manual") or "").strip()
        if ie_manual:
            empresa.inscricao_estadual = "ISENTO" if ie_manual.upper() == "ISENTO" else somente_digitos(ie_manual)[:30]

        if dados_linha.get("inscricao_municipal") is not None and dados_linha.get("inscricao_municipal") != "":
            empresa.inscricao_municipal = dados_linha["inscricao_municipal"].strip()[:30]
        if dados_linha.get("suframa") is not None and dados_linha.get("suframa") != "":
            empresa.suframa = dados_linha["suframa"].strip()[:20]

        if dados_linha.get("contribuinte_icms", ""):
            empresa.contribuinte_icms = _interpretar_booleano(dados_linha.get("contribuinte_icms"), True)
        if dados_linha.get("contribuinte_ipi", ""):
            empresa.contribuinte_ipi = _interpretar_booleano(dados_linha.get("contribuinte_ipi"), False)
        if dados_linha.get("possui_regime_especial", ""):
            empresa.possui_regime_especial = _interpretar_booleano(dados_linha.get("possui_regime_especial"), False)
        if empresa.perfil_estabelecimento in Empresa.PERFIS_COM_PDF:
            empresa.possui_regime_especial = True

        if dados_linha.get("data_inicio_cliente"):
            empresa.data_inicio_cliente = dados_linha["data_inicio_cliente"].strip()[:10]
        if dados_linha.get("data_saida_cliente"):
            empresa.data_saida_cliente = dados_linha["data_saida_cliente"].strip()[:20]
        if dados_linha.get("resumo_cliente"):
            empresa.resumo_cliente = dados_linha["resumo_cliente"].strip()

        for campo_qtd in ("qtd_notas_entrada", "qtd_notas_saida", "qtd_emissao_notas_servico", "qtd_emissao_notas_produto"):
            if dados_linha.get(campo_qtd):
                setattr(empresa, campo_qtd, dados_linha[campo_qtd].strip()[:40])

        contatos_lidos = _interpretar_contatos_planilha(dados_linha.get("contatos_texto", ""))
        if contatos_lidos:
            empresa.contatos = contatos_lidos

        chk_linha: dict[str, set[str]] = {
            "Fiscal": set(),
            "Contábil": set(),
            "DP": set(),
            "Regularização": set(),
        }
        for c_idx, (grp, item_nome) in cols_checklist.items():
            if c_idx < len(row) and _interpretar_booleano(row[c_idx], False):
                chk_linha[grp].add(item_nome)

        if cols_checklist or eh_novo:
            empresa.escopo_fiscal = {item: (item in chk_linha["Fiscal"]) for item in ITENS_FISCAIS}
            empresa.escopo_contabil = {item: (item in chk_linha["Contábil"]) for item in ITENS_CONTABEIS}
            empresa.escopo_dp = {item: (item in chk_linha["DP"]) for item in ITENS_DP}
            empresa.escopo_regularizacao = {item: (item in chk_linha["Regularização"]) for item in ITENS_REGULARIZACAO}

        if dados_linha.get("tipos_icms_incentivado"):
            partes_tts = [p.strip().upper() for p in re.split(r"[;\n]+", dados_linha["tipos_icms_incentivado"]) if p.strip()]
            tts_validos = [op for op in OPCOES_TTS if any(p == op.upper() or p in op.upper() for p in partes_tts)]
            if tts_validos:
                empresa.tipos_icms_incentivado = tts_validos
                empresa.escopo_fiscal["ICMS Incentivado"] = True

        if dados_linha.get("estados_icms_st"):
            ufs_icms_st = [u.strip().upper() for u in re.split(r"[;,\s]+", dados_linha["estados_icms_st"]) if u.strip().upper() in ESTADOS_BR]
            if ufs_icms_st:
                empresa.estados_icms_st = ufs_icms_st
                empresa.escopo_fiscal["Apuração: ICMS-ST"] = True

        if dados_linha.get("estados_st"):
            ufs_st = [u.strip().upper() for u in re.split(r"[;,\s]+", dados_linha["estados_st"]) if u.strip().upper() in ESTADOS_BR]
            if ufs_st:
                empresa.estados_st = ufs_st
                empresa.escopo_regularizacao["Inscrições de Substituto Tributário (ST)"] = True

        empresa.save()

        # Grava Inscrições de Substituto Tributário trazidas automaticamente pela consulta de CNPJ
        for uf_sigla, num_ie in substituicoes_api.items():
            num_limpo = somente_digitos(str(num_ie))
            if uf_sigla in ESTADOS_BR and num_limpo:
                InscricaoSubstituto.objects.update_or_create(
                    empresa=empresa,
                    uf=uf_sigla,
                    defaults={"numero": num_limpo},
                )

        try:
            criar_pastas(empresa)
        except Exception:
            pass

        if usuario is not None:
            try:
                registrar_empresa(usuario, antes, empresa)
            except Exception:
                pass

        if eh_novo:
            criados += 1
        else:
            atualizados += 1

    if usuario is not None and (criados or atualizados):
        try:
            registrar(
                usuario,
                None,
                "CADASTRO",
                "Importação de Clientes em Lote",
                "",
                f"Criados: {criados} | Atualizados: {atualizados}",
            )
        except Exception:
            pass

    return {
        "ok": (criados + atualizados) > 0 or not erros,
        "criados": criados,
        "atualizados": atualizados,
        "erros": erros,
    }


def gerar_planilha_relatorio_completo_clientes(queryset=None) -> bytes:
    """Extrai para os usuários uma planilha completa dos clientes (filtrados ou todos) e setores."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatorio_Clientes_Setores"

    fonte_titulo = Font(name="Segoe UI", size=12, bold=True, color="FFFFFF")
    fonte_cab = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
    fonte_celula = Font(name="Segoe UI", size=10, color="111827")
    preench_titulo = PatternFill("solid", fgColor="9D174D")
    preench_cab_cad = PatternFill("solid", fgColor="DB2777")
    borda_fina = Border(
        left=Side(style="thin", color="D6D3D1"),
        right=Side(style="thin", color="D6D3D1"),
        top=Side(style="thin", color="D6D3D1"),
        bottom=Side(style="thin", color="D6D3D1"),
    )

    colunas_relatorio = [
        ("codigo_dominio", "Código Domínio", 16),
        ("cnpj", "CNPJ", 20),
        ("tipo_estabelecimento", "Matriz / Filial", 16),
        ("status", "Situação", 14),
        ("data_saida_cliente", "Data de Saída (se Inativo)", 22),
        ("razao_social", "Razão Social", 36),
        ("nome_fantasia", "Nome Fantasia", 26),
        ("ramo_atividade", "Ramo de Atividade", 26),
        ("cnae_principal", "CNAE Principal", 16),
        ("cnae_descricao", "Descrição do CNAE", 34),
        ("data_abertura", "Data de Abertura", 18),
        ("data_inicio_cliente", "Cliente desde", 16),
        ("regime_tributario", "Regime Tributário", 22),
        ("regime_pis_cofins", "Regime PIS/COFINS", 20),
        ("anexo_simples", "Anexo Simples", 16),
        ("perfil_estabelecimento", "Perfil / Regime Especial", 24),
        ("responsavel_apuracao", "Operacional — Departamento Fiscal", 26),
        ("responsavel_revisao", "Revisador — Departamento Fiscal", 26),
        ("responsavel_operacional_contabil", "Operacional — Departamento Contábil", 28),
        ("responsavel_revisador_contabil", "Revisador — Departamento Contábil", 28),
        ("responsavel_operacional_dp", "Operacional — Departamento Pessoal", 28),
        ("responsavel_revisador_dp", "Revisador — Departamento Pessoal", 28),
        ("inscricao_estadual", "Inscrição Estadual", 20),
        ("ies_st", "IEs Substituto Tributário (0015)", 32),
        ("inscricao_municipal", "Inscrição Municipal", 20),
        ("suframa", "SUFRAMA", 16),
        ("cep", "CEP", 14),
        ("uf", "UF", 10),
        ("municipio", "Município", 22),
        ("codigo_ibge", "Código IBGE", 16),
        ("logradouro", "Logradouro", 32),
        ("numero", "Número", 12),
        ("complemento", "Complemento", 18),
        ("bairro", "Bairro", 20),
        ("contatos_texto", "Contatos dos Responsáveis", 46),
        ("resumo_cliente", "Resumo / Histórico", 36),
        ("tipos_icms_incentivado", "Incentivos TTS", 32),
        ("estados_icms_st", "UFs Apuração ICMS-ST", 22),
        ("estados_st", "UFs Inscrição ST", 22),
    ]

    total_cols = len(colunas_relatorio) + len(COLUNAS_CHECKLIST_UNICAS)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(total_cols, 30))
    c_tit = ws.cell(
        row=1,
        column=1,
        value="SENTINELA NASCEL — RELATÓRIO COMPLETO DE CLIENTES E ESCOPO DE TODOS OS DEPARTAMENTOS",
    )
    c_tit.font = fonte_titulo
    c_tit.fill = preench_titulo
    ws.row_dimensions[1].height = 28

    ws.row_dimensions[2].height = 34
    for idx, (_ch, titulo, larg) in enumerate(colunas_relatorio, start=1):
        c = ws.cell(row=2, column=idx, value=titulo)
        c.font = fonte_cab
        c.fill = preench_cab_cad
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = borda_fina
        ws.column_dimensions[get_column_letter(idx)].width = larg

    offset_chk = len(colunas_relatorio)
    for idx_c, (grupo, _item_nome, rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_chk + 1):
        cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
        c = ws.cell(row=2, column=idx_c, value=rotulo_col)
        c.font = fonte_cab
        c.fill = cor_grupo["cab"]
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = borda_fina
        ws.column_dimensions[get_column_letter(idx_c)].width = 24

    base_qs = queryset if queryset is not None else Empresa.objects.all()
    empresas = (
        base_qs.select_related(
            "responsavel_apuracao",
            "responsavel_revisao",
            "responsavel_operacional_contabil",
            "responsavel_revisador_contabil",
            "responsavel_operacional_dp",
            "responsavel_revisador_dp",
        )
        .prefetch_related("substituicoes")
        .order_by("codigo_dominio")
    )
    linha = 3
    for emp in empresas:
        ies_st_str = "; ".join(f"{s.uf}:{s.numero}" for s in emp.substituicoes.all())
        valores = {
            "codigo_dominio": emp.codigo_dominio,
            "cnpj": emp.cnpj,
            "tipo_estabelecimento": emp.tipo_estabelecimento,
            "status": emp.status,
            "data_saida_cliente": emp.data_saida_cliente or "",
            "razao_social": emp.razao_social,
            "nome_fantasia": emp.nome_fantasia or "",
            "ramo_atividade": emp.ramo_atividade or "",
            "cnae_principal": emp.cnae_principal or "",
            "cnae_descricao": emp.cnae_descricao or "",
            "data_abertura": emp.data_abertura or "",
            "data_inicio_cliente": emp.data_inicio_cliente or "",
            "regime_tributario": emp.regime_tributario,
            "regime_pis_cofins": emp.regime_pis_cofins,
            "anexo_simples": emp.anexo_simples or "",
            "perfil_estabelecimento": emp.perfil_estabelecimento,
            "responsavel_apuracao": emp.responsavel_apuracao.nome if emp.responsavel_apuracao else "",
            "responsavel_revisao": emp.responsavel_revisao.nome if emp.responsavel_revisao else "",
            "responsavel_operacional_contabil": emp.responsavel_operacional_contabil.nome if emp.responsavel_operacional_contabil else "",
            "responsavel_revisador_contabil": emp.responsavel_revisador_contabil.nome if emp.responsavel_revisador_contabil else "",
            "responsavel_operacional_dp": emp.responsavel_operacional_dp.nome if emp.responsavel_operacional_dp else "",
            "responsavel_revisador_dp": emp.responsavel_revisador_dp.nome if emp.responsavel_revisador_dp else "",
            "inscricao_estadual": emp.inscricao_estadual or "",
            "ies_st": ies_st_str,
            "inscricao_municipal": emp.inscricao_municipal or "",
            "suframa": emp.suframa or "",
            "cnae_principal": emp.cnae_principal or "",
            "cep": emp.cep or "",
            "uf": emp.uf or "",
            "municipio": emp.municipio or "",
            "codigo_ibge": emp.codigo_ibge or "",
            "logradouro": emp.logradouro or "",
            "numero": emp.numero or "",
            "complemento": emp.complemento or "",
            "bairro": emp.bairro or "",
            "contatos_texto": _formatar_contatos_planilha(emp.contatos),
            "resumo_cliente": emp.resumo_cliente or "",
            "tipos_icms_incentivado": "; ".join(emp.tipos_icms_incentivado or []),
            "estados_icms_st": "; ".join(emp.estados_icms_st or []),
            "estados_st": "; ".join(emp.estados_st or []),
        }
        for col_idx, (chave, _tit, _larg) in enumerate(colunas_relatorio, start=1):
            cel = ws.cell(row=linha, column=col_idx, value=valores.get(chave, ""))
            cel.font = fonte_celula
            cel.border = borda_fina
            cel.number_format = "@"
        for idx_c, (grupo, item_nome, _rotulo_col) in enumerate(COLUNAS_CHECKLIST_UNICAS, start=offset_chk + 1):
            cor_grupo = CORES_CHECKLIST.get(grupo, CORES_CHECKLIST["Fiscal"])
            mapa = (
                emp.escopo_fiscal if grupo == "Fiscal"
                else emp.escopo_contabil if grupo == "Contábil"
                else emp.escopo_dp if grupo == "DP"
                else emp.escopo_regularizacao
            ) or {}
            cel_chk = ws.cell(row=linha, column=idx_c, value="Sim" if mapa.get(item_nome) else "")
            cel_chk.font = fonte_celula
            cel_chk.fill = cor_grupo["cel"]
            cel_chk.alignment = Alignment(horizontal="center")
            cel_chk.border = borda_fina
        linha += 1

    ws.freeze_panes = "C3"
    ws.auto_filter.ref = f"A2:{get_column_letter(total_cols)}{max(linha, 10)}"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
