"""Painel do Garimpeiro no Sentinela: estilo Excel com filtros, cópia, buracos, séries e extração."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from collections import defaultdict
from datetime import date, datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from clientes.models import DocumentoXML
from clientes.utils_sharepoint import pasta_do_mes

ABAS_TELA = (
    ("lidos", "O que foi lido"),
    ("series", "Séries lidas"),
    ("buracos", "Buracos"),
    ("autorizadas", "Autorizadas"),
    ("canceladas", "Canceladas"),
    ("inutilizadas", "Inutilizadas"),
    ("denegadas", "Denegadas"),
    ("rejeitadas", "Rejeitadas"),
)
ABAS = (
    "lidos",
    "series",
    "buracos",
    "autorizadas",
    "canceladas",
    "inutilizadas",
    "denegadas",
    "rejeitadas",
)
STATUS_DA_ABA = {
    "autorizadas": ("AUTORIZADA",),
    "canceladas": ("CANCELADA",),
    "inutilizadas": ("INUTILIZADA",),
    "denegadas": ("DENEGADA",),
    "rejeitadas": ("OUTROS", "REJEITADA"),
}
PROPRIAS = {"SAIDA_PROPRIA", "ENTRADA_EMISSAO_PROPRIA"}
SALTO_MAXIMO = 25000
LIMITE_TELA = 3000

MODELOS_ROTULO = {
    "55": "NF-e (55)",
    "65": "NFC-e (65)",
    "57": "CT-e (57)",
    "67": "CT-e OS (67)",
}


def _vazio(mensagem: str = "") -> dict:
    return {
        "sem_arquivo": True,
        "erro": mensagem,
        "resumo": {"total": 0, "propria": 0, "terceiros": 0},
        "contagens": {},
        "contagens_abas": {codigo: 0 for codigo, _ in ABAS_TELA},
        "buracos_reais": 0,
        "buracos_falsos": 0,
        "linhas": [],
        "linhas_falsas": [],
        "series": [],
        "colunas": [],
        "linhas_grade": [],
        "mostrando": 0,
        "total_aba": 0,
        "xml_no_disco": 0,
        "pasta": "",
    }


def _consulta(empresa, competencia: str):
    return DocumentoXML.objects.filter(empresa=empresa, competencia=competencia)


def _rotulo_modelo(mod: str) -> str:
    m = str(mod or "55").strip()
    return MODELOS_ROTULO.get(m, m)


def _rotulo_origem(nota) -> str:
    return "Emissão própria" if nota.tipo_operacao in PROPRIAS else "Terceiros"


def _rotulo_operacao(tipo_op: str) -> str:
    mapa = {
        "SAIDA_PROPRIA": "Saída própria",
        "ENTRADA_EMISSAO_PROPRIA": "Entrada própria",
        "ENTRADA_TERCEIROS": "Entrada terceiros",
    }
    return mapa.get(tipo_op, tipo_op or "")


def _linha(nota) -> dict:
    return {
        "modelo": nota.modelo,
        "modelo_rotulo": _rotulo_modelo(nota.modelo),
        "serie": nota.serie,
        "numero": nota.numero_nota,
        "data_emissao": nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
        "status": nota.status_sefa,
        "is_propria": nota.tipo_operacao in PROPRIAS,
        "origem": _rotulo_origem(nota),
        "operacao": _rotulo_operacao(nota.tipo_operacao),
        "valor_total": f"{nota.valor_total:.2f}".replace(".", ","),
        "valor_icms": f"{nota.valor_icms:.2f}".replace(".", ","),
        "chave": nota.chave_acesso,
        "nome_arquivo": "",
        "caminho_relativo": "",
    }


def _buracos(notas, empresa=None, competencia: str = "") -> tuple[list[dict], list[dict]]:
    grupos: dict[tuple[str, str], set[int]] = defaultdict(set)
    todos_vistos: dict[tuple[str, str], set[int]] = defaultdict(set)
    for nota in notas:
        if nota.tipo_operacao not in PROPRIAS:
            continue
        try:
            numero = int(str(nota.numero_nota).lstrip("0") or "0")
        except ValueError:
            continue
        todos_vistos[(nota.modelo, nota.serie)].add(numero)
        if nota.status_sefa in {"CANCELADA", "DENEGADA", "OUTROS", "REJEITADA", "INUTILIZADA"}:
            continue
        grupos[(nota.modelo, nota.serie)].add(numero)

    # Mapa de notas de outros meses da mesma empresa para localizar "buracos falsos"
    outros_meses_map: dict[tuple[str, str, int], DocumentoXML] = {}
    if empresa is not None:
        for outra in DocumentoXML.objects.filter(empresa=empresa).exclude(competencia=competencia):
            if outra.tipo_operacao not in PROPRIAS:
                continue
            try:
                num_o = int(str(outra.numero_nota).lstrip("0") or "0")
            except ValueError:
                continue
            outros_meses_map[(outra.modelo, outra.serie, num_o)] = outra

    faltas = []
    localizadas_outro_mes = []
    for (modelo, serie), numeros in sorted(todos_vistos.items()):
        ordenados = sorted(numeros)
        for anterior, seguinte in zip(ordenados, ordenados[1:]):
            quantidade = seguinte - anterior - 1
            if quantidade <= 0 or quantidade > SALTO_MAXIMO:
                continue
            # Verificar se algum número individual está em outro mês
            faltantes_reais = []
            for num_faltante in range(anterior + 1, seguinte):
                achada = outros_meses_map.get((modelo, serie, num_faltante))
                if achada is not None:
                    comp_a = achada.competencia or ""
                    comp_fmt = f"{comp_a[:2]}/{comp_a[2:]}" if len(comp_a) == 6 else comp_a
                    localizadas_outro_mes.append({
                        "tipo": _rotulo_modelo(modelo),
                        "serie": serie,
                        "numero": str(num_faltante),
                        "situacao": f"Emitida em {comp_fmt} ({achada.status_sefa})",
                        "data_emissao": achada.data_emissao.strftime("%d/%m/%Y") if achada.data_emissao else "—",
                        "buraco_em": f"{competencia[:2]}/{competencia[2:]}" if len(competencia) == 6 else competencia,
                        "emitida_em": comp_fmt,
                        "status": achada.status_sefa,
                        "chave": achada.chave_acesso,
                    })
                else:
                    faltantes_reais.append(num_faltante)

            if faltantes_reais:
                ini_f = faltantes_reais[0]
                fim_f = faltantes_reais[-1]
                qtd_f = len(faltantes_reais)
                texto = str(ini_f) if qtd_f == 1 else f"{ini_f} a {fim_f}"
                faltas.append({
                    "tipo": _rotulo_modelo(modelo),
                    "serie": serie,
                    "numero": texto,
                    "periodo": f"{qtd_f} nota(s)",
                    "situacao": f"Falta no arquivo ({qtd_f} nota)" if qtd_f == 1 else f"Falta no arquivo ({qtd_f} notas)",
                    "data_emissao": "—",
                    "mes_possivel": "",
                    "buraco_em": "",
                    "emitida_em": "",
                    "status": "FALTA",
                })
    return faltas, localizadas_outro_mes


def _series(notas) -> list[dict]:
    """Monta a tabela de Séries lidas idêntica ao Garimpeiro Desktop."""
    grupos: dict[tuple[str, str, str], dict] = {}
    numeros_por_serie: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    for nota in notas:
        origem = "Emissão própria" if nota.tipo_operacao in PROPRIAS else "Terceiros"
        chave = (origem, nota.modelo, nota.serie)
        item = grupos.setdefault(chave, {
            "origem": origem,
            "modelo": nota.modelo,
            "tipo": _rotulo_modelo(nota.modelo),
            "serie": nota.serie,
            "n_ini": "—",
            "n_fim": "—",
            "total": 0,
            "buracos": 0,
            "autorizadas": 0,
            "canceladas": 0,
            "inutilizadas": 0,
            "denegadas": 0,
            "rejeitadas": 0,
        })
        item["total"] += 1
        try:
            num_int = int(str(nota.numero_nota).lstrip("0") or "0")
            if num_int > 0:
                numeros_por_serie[chave].append(num_int)
        except ValueError:
            pass

        if nota.status_sefa == "AUTORIZADA":
            item["autorizadas"] += 1
        elif nota.status_sefa == "CANCELADA":
            item["canceladas"] += 1
        elif nota.status_sefa == "INUTILIZADA":
            item["inutilizadas"] += 1
        elif nota.status_sefa == "DENEGADA":
            item["denegadas"] += 1
        else:
            item["rejeitadas"] += 1

    for chave, item in grupos.items():
        nums = sorted(set(numeros_por_serie.get(chave) or []))
        if nums:
            item["n_ini"] = str(nums[0])
            item["n_fim"] = str(nums[-1])
            if chave[0] == "Emissão própria" and len(nums) > 1:
                bur = 0
                for a, b in zip(nums, nums[1:]):
                    salto = b - a - 1
                    if 0 < salto <= SALTO_MAXIMO:
                        bur += salto
                item["buracos"] = bur

    return [grupos[chave] for chave in sorted(grupos)]


def _montar_grade_excel(aba: str, series: list[dict], buracos: list[dict], falsos: list[dict], linhas_notas: list[dict]) -> tuple[list[dict], list[dict]]:
    """Devolve (colunas, linhas_grade) no formato de planilha Excel do Garimpeiro Desktop."""
    if aba == "series":
        colunas = [
            {"id": "origem", "titulo": "Origem", "largura": "w-36"},
            {"id": "tipo", "titulo": "Tipo", "largura": "w-28"},
            {"id": "serie", "titulo": "Série", "largura": "w-20"},
            {"id": "n_ini", "titulo": "1ª nota", "largura": "w-24"},
            {"id": "n_fim", "titulo": "Últ. nota", "largura": "w-24"},
            {"id": "total", "titulo": "Qtd", "largura": "w-20"},
            {"id": "buracos", "titulo": "Bur.", "largura": "w-20"},
            {"id": "autorizadas", "titulo": "Norm.", "largura": "w-20"},
            {"id": "canceladas", "titulo": "Canc.", "largura": "w-20"},
            {"id": "inutilizadas", "titulo": "Inut.", "largura": "w-20"},
        ]
        linhas = []
        for s in series:
            linhas.append({
                "chave": "",
                "alerta": s["buracos"] > 0 or s["canceladas"] > 0,
                "celulas": [
                    str(s["origem"]),
                    str(s["tipo"]),
                    str(s["serie"]),
                    str(s["n_ini"]),
                    str(s["n_fim"]),
                    str(s["total"]),
                    str(s["buracos"]),
                    str(s["autorizadas"]),
                    str(s["canceladas"]),
                    str(s["inutilizadas"]),
                ],
            })
        return colunas, linhas

    if aba == "buracos":
        colunas = [
            {"id": "tipo", "titulo": "Tipo", "largura": "w-28"},
            {"id": "serie", "titulo": "Série", "largura": "w-20"},
            {"id": "numero", "titulo": "Nº / Faixa", "largura": "w-32"},
            {"id": "situacao", "titulo": "Situação", "largura": "w-64"},
            {"id": "data_emissao", "titulo": "Data emissão", "largura": "w-28"},
        ]
        linhas = []
        for f in falsos:
            linhas.append({
                "chave": f.get("chave", ""),
                "alerta": False,
                "celulas": [
                    str(f["tipo"]),
                    str(f["serie"]),
                    str(f["numero"]),
                    str(f["situacao"]),
                    str(f["data_emissao"]),
                ],
            })
        for b in buracos:
            linhas.append({
                "chave": "",
                "alerta": True,
                "celulas": [
                    str(b["tipo"]),
                    str(b["serie"]),
                    str(b["numero"]),
                    str(b["situacao"]),
                    str(b["data_emissao"]),
                ],
            })
        return colunas, linhas

    # Abas de notas (Autorizadas, Canceladas, Inutilizadas, Denegadas, Rejeitadas, Todas)
    colunas = [
        {"id": "origem", "titulo": "Origem", "largura": "w-36"},
        {"id": "modelo", "titulo": "Modelo", "largura": "w-24"},
        {"id": "serie", "titulo": "Série", "largura": "w-16"},
        {"id": "numero", "titulo": "Nota", "largura": "w-24"},
        {"id": "data_emissao", "titulo": "Data emissão", "largura": "w-28"},
        {"id": "status", "titulo": "Status", "largura": "w-28"},
        {"id": "valor_total", "titulo": "Valor (R$)", "largura": "w-28"},
        {"id": "chave", "titulo": "Chave de Acesso", "largura": "w-96"},
    ]
    linhas = []
    for n in linhas_notas:
        linhas.append({
            "chave": str(n["chave"]),
            "alerta": n["status"] in {"CANCELADA", "DENEGADA", "OUTROS", "REJEITADA"},
            "celulas": [
                str(n["origem"]),
                str(n["modelo_rotulo"]),
                str(n["serie"]),
                str(n["numero"]),
                str(n["data_emissao"]),
                str(n["status"]),
                str(n["valor_total"]),
                str(n["chave"]),
            ],
        })
    return colunas, linhas


def montar(empresa, competencia: str, aba: str) -> dict:
    notas = list(_consulta(empresa, competencia))
    painel = _vazio()
    if not notas:
        outras = DocumentoXML.objects.filter(empresa=empresa).exclude(competencia=competencia).count()
        if outras:
            painel["erro"] = f"Nesta competência não há notas. Esta empresa tem {outras} nota(s) em outro mês. Troque a competência no topo."
        else:
            painel["erro"] = "A triagem desta empresa ainda não leu notas nesta competência."
        return painel
    propria = sum(1 for nota in notas if nota.tipo_operacao in PROPRIAS)
    contagens: dict[str, int] = {}
    for nota in notas:
        contagens[nota.status_sefa] = contagens.get(nota.status_sefa, 0) + 1

    series = _series(notas)
    buracos_reais, buracos_falsos = _buracos(notas, empresa=empresa, competencia=competencia)

    contagens_abas = {
        "series": len(series),
        "buracos": len(buracos_reais) + len(buracos_falsos),
        "autorizadas": contagens.get("AUTORIZADA", 0),
        "canceladas": contagens.get("CANCELADA", 0),
        "inutilizadas": contagens.get("INUTILIZADA", 0),
        "denegadas": contagens.get("DENEGADA", 0),
        "rejeitadas": contagens.get("OUTROS", 0) + contagens.get("REJEITADA", 0),
        "lidos": len(notas),
    }

    painel["sem_arquivo"] = False
    painel["erro"] = ""
    painel["contagens"] = contagens
    painel["contagens_abas"] = contagens_abas
    painel["resumo"] = {"total": len(notas), "propria": propria, "terceiros": len(notas) - propria}
    painel["series"] = series
    painel["buracos_reais"] = len(buracos_reais)
    painel["buracos_falsos"] = len(buracos_falsos)
    painel["linhas_falsas"] = buracos_falsos

    escolhidas = notas
    status = STATUS_DA_ABA.get(aba)
    if status:
        escolhidas = [nota for nota in notas if nota.status_sefa in status]
    linhas_notas = [_linha(nota) for nota in escolhidas]

    if aba == "buracos":
        painel["linhas"] = buracos_reais[:LIMITE_TELA]
        painel["total_aba"] = len(buracos_reais) + len(buracos_falsos)
        painel["mostrando"] = len(painel["linhas"]) + len(buracos_falsos)
    elif aba == "series":
        painel["total_aba"] = len(series)
        painel["mostrando"] = len(series)
    else:
        painel["total_aba"] = len(linhas_notas)
        painel["linhas"] = linhas_notas[:LIMITE_TELA]
        painel["mostrando"] = len(painel["linhas"])

    colunas, linhas_grade = _montar_grade_excel(
        aba,
        series,
        buracos_reais[:LIMITE_TELA],
        buracos_falsos[:LIMITE_TELA],
        linhas_notas[:LIMITE_TELA],
    )
    painel["colunas"] = colunas
    painel["linhas_grade"] = linhas_grade
    return painel


def _estilizar_aba_xlsx(ws, titulo_topo: str, cabecalhos: list[str], linhas: list[list], larguras: list[int]):
    rosa_cab = PatternFill("solid", fgColor="Be185D")
    zebra = PatternFill("solid", fgColor="FDF2F8")
    alerta_fill = PatternFill("solid", fgColor="FEE2E2")
    fonte_cab = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    fonte = Font(name="Calibri", size=10)
    borda = Border(
        left=Side(style="thin", color="E5E7EB"),
        right=Side(style="thin", color="E5E7EB"),
        top=Side(style="thin", color="E5E7EB"),
        bottom=Side(style="thin", color="E5E7EB"),
    )

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(len(cabecalhos), 3))
    ws["A1"] = titulo_topo
    ws["A1"].font = Font(name="Calibri", bold=True, size=12, color="BE185D")
    ws["A1"].alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 28

    for col_idx, texto in enumerate(cabecalhos, start=1):
        cel = ws.cell(2, col_idx, texto)
        cel.fill = rosa_cab
        cel.font = fonte_cab
        cel.alignment = Alignment(horizontal="center", vertical="center")
        cel.border = borda

    for row_idx, valores in enumerate(linhas, start=3):
        eh_cancelada = any(str(v).upper() in {"CANCELADA", "DENEGADA", "FALTA"} for v in valores)
        for col_idx, val in enumerate(valores, start=1):
            cel = ws.cell(row_idx, col_idx, val)
            cel.font = fonte
            cel.border = borda
            if eh_cancelada:
                cel.fill = alerta_fill
            elif row_idx % 2 == 1:
                cel.fill = zebra

    for i, larg in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = larg

    ultima_linha = max(2, 2 + len(linhas))
    ultima_letra = get_column_letter(len(cabecalhos))
    ws.auto_filter.ref = f"A2:{ultima_letra}{ultima_linha}"
    ws.freeze_panes = "A3"


def planilha_xlsx_bytes(empresa, competencia: str, aba: str = "completo", chaves_filtro: set[str] | None = None) -> bytes:
    """Gera o arquivo Excel (.xlsx) real e formatado do Garimpeiro (mês completo ou aba específica)."""
    notas = list(_consulta(empresa, competencia))
    if chaves_filtro:
        notas = [n for n in notas if n.chave_acesso in chaves_filtro]

    series = _series(notas)
    buracos_reais, buracos_falsos = _buracos(notas, empresa=empresa, competencia=competencia)
    comp_fmt = f"{competencia[:2]}/{competencia[2:]}" if len(competencia) == 6 else competencia
    ident = f"{empresa.codigo_dominio} - {empresa.razao_social} ({empresa.cnpj}) · Competência {comp_fmt}"

    wb = Workbook()
    ws_inicial = wb.active

    def _adicionar_aba_series(ws):
        ws.title = "Séries lidas"
        cabs = ["Origem", "Tipo", "Série", "1ª nota", "Últ. nota", "Qtd", "Bur.", "Norm.", "Canc.", "Inut."]
        rows = [
            [s["origem"], s["tipo"], s["serie"], s["n_ini"], s["n_fim"], s["total"], s["buracos"], s["autorizadas"], s["canceladas"], s["inutilizadas"]]
            for s in series
        ]
        _estilizar_aba_xlsx(ws, f"Garimpeiro · Séries lidas — {ident}", cabs, rows, [20, 14, 10, 14, 14, 10, 10, 10, 10, 10])

    def _adicionar_aba_buracos(ws):
        ws.title = "Buracos"
        cabs = ["Tipo", "Série", "Nº / Faixa", "Situação", "Data emissão"]
        rows = [
            [f["tipo"], f["serie"], f["numero"], f["situacao"], f["data_emissao"]]
            for f in buracos_falsos
        ] + [
            [b["tipo"], b["serie"], b["numero"], b["situacao"], b["data_emissao"]]
            for b in buracos_reais
        ]
        _estilizar_aba_xlsx(ws, f"Garimpeiro · Buracos de Numeração — {ident}", cabs, rows, [16, 10, 22, 38, 16])

    def _adicionar_aba_notas(ws, titulo_aba: str, lista_notas: list):
        ws.title = titulo_aba[:31]
        cabs = ["Origem", "Modelo", "Série", "Nota", "Data Emissão", "Status", "Valor Total (R$)", "Base ICMS (R$)", "Valor ICMS (R$)", "Chave de Acesso"]
        rows = [
            [
                _rotulo_origem(n),
                _rotulo_modelo(n.modelo),
                n.serie,
                n.numero_nota,
                n.data_emissao.strftime("%d/%m/%Y") if n.data_emissao else "",
                n.status_sefa,
                float(n.valor_total),
                float(n.base_icms),
                float(n.valor_icms),
                n.chave_acesso,
            ]
            for n in lista_notas
        ]
        _estilizar_aba_xlsx(ws, f"Garimpeiro · {titulo_aba} — {ident}", cabs, rows, [20, 14, 10, 14, 16, 16, 18, 18, 18, 48])

    if aba == "series":
        _adicionar_aba_series(ws_inicial)
    elif aba == "buracos":
        _adicionar_aba_buracos(ws_inicial)
    elif aba in STATUS_DA_ABA or aba == "lidos":
        st_filtro = STATUS_DA_ABA.get(aba)
        filtradas = [n for n in notas if (not st_filtro or n.status_sefa in st_filtro)]
        rotulo_aba = dict(ABAS_TELA).get(aba, "Notas")
        _adicionar_aba_notas(ws_inicial, rotulo_aba, filtradas)
    else:
        # Excel Completo do Mês (todas as abas igual ao Desktop!)
        _adicionar_aba_series(ws_inicial)
        _adicionar_aba_buracos(wb.create_sheet("Buracos"))
        _adicionar_aba_notas(wb.create_sheet("Autorizadas"), "Autorizadas", [n for n in notas if n.status_sefa == "AUTORIZADA"])
        _adicionar_aba_notas(wb.create_sheet("Canceladas"), "Canceladas", [n for n in notas if n.status_sefa == "CANCELADA"])
        _adicionar_aba_notas(wb.create_sheet("Inutilizadas"), "Inutilizadas", [n for n in notas if n.status_sefa == "INUTILIZADA"])
        _adicionar_aba_notas(wb.create_sheet("Denegadas"), "Denegadas", [n for n in notas if n.status_sefa == "DENEGADA"])
        _adicionar_aba_notas(wb.create_sheet("Rejeitadas"), "Rejeitadas", [n for n in notas if n.status_sefa in {"OUTROS", "REJEITADA"}])
        _adicionar_aba_notas(wb.create_sheet("Todas as Notas"), "Todas as Notas", notas)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def planilha(empresa, competencia: str, aba: str) -> str:
    notas = list(_consulta(empresa, competencia))
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    if aba == "buracos":
        escritor.writerow(["Tipo", "Série", "Números faltantes", "Situação", "Data emissão"])
        bur_reais, bur_falsos = _buracos(notas, empresa=empresa, competencia=competencia)
        for linha in bur_falsos:
            escritor.writerow([linha["tipo"], linha["serie"], linha["numero"], linha["situacao"], linha["data_emissao"]])
        for linha in bur_reais:
            escritor.writerow([linha["tipo"], linha["serie"], linha["numero"], linha["situacao"], linha["data_emissao"]])
        return saida.getvalue()
    if aba == "series":
        escritor.writerow(["Origem", "Tipo", "Série", "1ª nota", "Últ. nota", "Qtd", "Bur.", "Norm.", "Canc.", "Inut."])
        for linha in _series(notas):
            escritor.writerow([
                linha["origem"], linha["tipo"], linha["serie"], linha["n_ini"], linha["n_fim"],
                linha["total"], linha["buracos"], linha["autorizadas"], linha["canceladas"], linha["inutilizadas"],
            ])
        return saida.getvalue()
    escritor.writerow(["Origem", "Modelo", "Série", "Nota", "Emissão", "Status", "Valor", "Chave"])
    status = STATUS_DA_ABA.get(aba)
    for nota in notas:
        if status and nota.status_sefa not in status:
            continue
        escritor.writerow([
            _rotulo_origem(nota),
            nota.modelo,
            nota.serie,
            nota.numero_nota,
            nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
            nota.status_sefa,
            nota.valor_total,
            nota.chave_acesso,
        ])
    return saida.getvalue()


def _mapa_arquivos_xml_mes(empresa, competencia: str) -> dict[str, Path]:
    """Indexa todos os XMLs da pasta do mês por chave de 44 dígitos em uma única varredura rápida."""
    mapa: dict[str, Path] = {}
    try:
        raiz = pasta_do_mes(empresa, competencia)
    except ValueError:
        return mapa
    if not raiz.is_dir():
        return mapa
    for item in raiz.rglob("*.xml"):
        if not item.is_file():
            continue
        nome = item.name
        achadas = re.findall(r"\d{44}", nome)
        for ch in achadas:
            mapa.setdefault(ch, item)
    return mapa


def zip_xml(
    empresa,
    competencia: str,
    aba: str,
    *,
    chaves_especificas: list[str] | None = None,
    filtros_recorte: dict | None = None,
) -> tuple[bytes, int, int]:
    if aba in {"buracos", "series"} and not chaves_especificas and not filtros_recorte:
        return b"", 0, 0
    notas = list(_consulta(empresa, competencia))

    if chaves_especificas:
        alvo_set = {re.sub(r"\D", "", c) for c in chaves_especificas if len(re.sub(r"\D", "", c)) == 44}
        notas = [n for n in notas if n.chave_acesso in alvo_set]
    elif filtros_recorte:
        origem_f = (filtros_recorte.get("origem") or "").strip()
        status_f = (filtros_recorte.get("status") or "").strip()
        serie_f = (filtros_recorte.get("serie") or "").strip()
        modelo_f = (filtros_recorte.get("modelo") or "").strip()
        nota_ini = (filtros_recorte.get("nota_ini") or "").strip()
        nota_fim = (filtros_recorte.get("nota_fim") or "").strip()
        data_ini = (filtros_recorte.get("data_ini") or "").strip()
        data_fim = (filtros_recorte.get("data_fim") or "").strip()

        filtradas = []
        for n in notas:
            if origem_f == "propria" and n.tipo_operacao not in PROPRIAS:
                continue
            if origem_f == "terceiros" and n.tipo_operacao in PROPRIAS:
                continue
            if status_f and status_f != "TODOS" and n.status_sefa != status_f:
                continue
            if serie_f and n.serie != serie_f:
                continue
            if modelo_f and modelo_f != "TODOS" and n.modelo != modelo_f:
                continue
            if nota_ini.isdigit() and n.numero_nota.isdigit() and int(n.numero_nota) < int(nota_ini):
                continue
            if nota_fim.isdigit() and n.numero_nota.isdigit() and int(n.numero_nota) > int(nota_fim):
                continue
            if data_ini:
                try:
                    dt_i = datetime.strptime(data_ini, "%d/%m/%Y").date()
                    if n.data_emissao and n.data_emissao < dt_i:
                        continue
                except ValueError:
                    pass
            if data_fim:
                try:
                    dt_f = datetime.strptime(data_fim, "%d/%m/%Y").date()
                    if n.data_emissao and n.data_emissao > dt_f:
                        continue
                except ValueError:
                    pass
            filtradas.append(n)
        notas = filtradas
    else:
        status = STATUS_DA_ABA.get(aba)
        if status:
            notas = [n for n in notas if n.status_sefa in status]

    mapa_arquivos = _mapa_arquivos_xml_mes(empresa, competencia)
    memoria = io.BytesIO()
    gravados = 0
    sem_arquivo = 0
    with zipfile.ZipFile(memoria, "w", compression=zipfile.ZIP_DEFLATED) as pacote:
        for nota in notas:
            caminho = mapa_arquivos.get(nota.chave_acesso)
            if caminho is None:
                sem_arquivo += 1
                continue
            pacote.write(caminho, arcname=caminho.name)
            gravados += 1
    return memoria.getvalue(), gravados, sem_arquivo
