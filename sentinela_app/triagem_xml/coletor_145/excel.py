from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.periodo import parse_data_br

CABECALHO = [
    "Número",
    "Chave de Acesso",
    "Data Emissão",
    "Competência de Emissão",
    "Razão_Emitente",
    "Código de Tributação Nacional",
    "Valor do Serviço",
    "Valor Líquido",
    "Retenção",
    "Valor do ISSQN",
    "Contribuições Sociais - Retidas",
    "Contribuição Previdenciária - Retida",
    "IRRF",
    "Status",
    "Data do Cancelamento",
]

# Colunas com valor em R$ (1-based): G, H, J, K, L, M
COLS_VALOR = (7, 8, 10, 11, 12, 13)
COL_STATUS = 14
COL_DATA_CANCEL = 15


def _parse_valor(texto: str) -> float | str:
    bruto = (texto or "").strip()
    if not bruto:
        return ""
    limpo = re.sub(r"[R$\s]", "", bruto, flags=re.I)
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return bruto


def _parse_data_celula(texto: str) -> datetime | str:
    bruto = (texto or "").strip()
    if not bruto:
        return ""
    so_data = bruto.split()[0] if bruto else bruto
    try:
        return datetime.combine(parse_data_br(so_data), datetime.min.time())
    except ValueError:
        return bruto


def _parse_data_hora(texto: str) -> datetime | str:
    bruto = (texto or "").strip()
    if not bruto:
        return ""
    for fmt in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(bruto[:19], fmt)
        except ValueError:
            continue
    return _parse_data_celula(bruto)


def gravar_planilha(notas: list[dict], destino: Path, *, rotulo_periodo: str) -> Path:
    destino.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "NFS-e Recebidas"

    verde = PatternFill("solid", fgColor="0F766E")
    zebra = PatternFill("solid", fgColor="F0FDFA")
    total_fill = PatternFill("solid", fgColor="ECFDF5")
    cancel_fill = PatternFill("solid", fgColor="FFD6D6")
    fonte_cab = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    fonte = Font(name="Calibri", size=11)
    fonte_cancel = Font(name="Calibri", size=11, color="9B1C1C")
    fonte_total = Font(name="Calibri", bold=True, size=11, color="0F766E")
    borda = Border(
        left=Side(style="thin", color="CCE5E0"),
        right=Side(style="thin", color="CCE5E0"),
        top=Side(style="thin", color="CCE5E0"),
        bottom=Side(style="thin", color="CCE5E0"),
    )

    ws.merge_cells("A1:C1")
    ws["A1"] = f"NFS-e Recebidas — {rotulo_periodo}"
    ws["A1"].font = Font(name="Calibri", bold=True, size=12, color="0F766E")
    ws["A1"].alignment = Alignment(vertical="center")

    ultima = 2 + max(len(notas), 1)
    for col in COLS_VALOR:
        letra = get_column_letter(col)
        cel = ws.cell(1, col, f"=SUBTOTAL(9,{letra}3:{letra}{ultima})")
        cel.font = fonte_total
        cel.fill = total_fill
        cel.number_format = '"R$" #,##0.00'
        cel.alignment = Alignment(horizontal="right", vertical="center")
        cel.border = borda

    for col, titulo in enumerate(CABECALHO, start=1):
        cel = ws.cell(2, col, titulo)
        cel.fill = verde
        cel.font = fonte_cab
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cel.border = borda

    for i, nota in enumerate(notas, start=3):
        valores = [
            (nota.get("numero") or "").strip(),
            (nota.get("chave_acesso") or "").strip(),
            _parse_data_celula(nota.get("data_emissao") or ""),
            (nota.get("competencia_emissao") or "").strip(),
            (nota.get("razao_emitente") or "").strip(),
            (nota.get("codigo_tributacao_nacional") or "").strip(),
            _parse_valor(nota.get("valor_servico") or ""),
            _parse_valor(nota.get("valor_liquido") or ""),
            (nota.get("retencao") or "").strip(),
            _parse_valor(nota.get("valor_issqn") or ""),
            _parse_valor(nota.get("contrib_sociais_retidas") or ""),
            _parse_valor(nota.get("contrib_previdenciaria_retida") or ""),
            _parse_valor(nota.get("irrf") or ""),
            (nota.get("status") or "Ativa").strip() or "Ativa",
            _parse_data_hora(nota.get("data_cancelamento") or ""),
        ]
        cancelada = str(valores[COL_STATUS - 1]).strip().lower() == "cancelada"
        for col, valor in enumerate(valores, start=1):
            cel = ws.cell(i, col, valor if valor != "" else None)
            cel.font = fonte_cancel if cancelada else fonte
            cel.border = borda
            if cancelada:
                cel.fill = cancel_fill
            elif i % 2 == 1:
                cel.fill = zebra
            if col == 3 and isinstance(valor, datetime):
                cel.number_format = "DD/MM/YYYY"
                cel.alignment = Alignment(horizontal="center")
            elif col == COL_DATA_CANCEL and isinstance(valor, datetime):
                cel.number_format = "DD/MM/YYYY HH:MM:SS" if (valor.hour or valor.minute or valor.second) else "DD/MM/YYYY"
                cel.alignment = Alignment(horizontal="center")
            elif col in COLS_VALOR and isinstance(valor, float):
                cel.number_format = '"R$" #,##0.00'
                cel.alignment = Alignment(horizontal="right")
            elif col in (1, COL_STATUS):
                cel.alignment = Alignment(horizontal="center")
            elif col == 6:
                cel.alignment = Alignment(wrap_text=True, vertical="top")

    larguras = [18, 32, 16, 18, 55, 72, 20, 20, 28, 18, 32, 36, 16, 14, 24]
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura

    ultima_filtro = max(2, 2 + len(notas))
    ws.auto_filter.ref = f"A2:O{ultima_filtro}"
    ws.freeze_panes = "A3"
    ws.row_dimensions[1].height = 22
    ws.row_dimensions[2].height = 32

    wb.save(destino)
    return destino


def nome_arquivo(pasta: Path, *, mes: int | None, ano: int | None, agora: datetime | None = None) -> Path:
    # Nome padrão solicitado para o arquivo do relatório exportado.
    # A janela do Windows mantém a possibilidade de o usuário alterar o nome
    # antes de salvar, usando o valor inicial sugerido pelo arquivo abaixo.
    base = "Relatório NFS-e Tomadas - Portal Nacional.xlsx"
    return pasta / base
