"""Motor integrado do Coletor de NFS-e v1.4.5 dentro do Sentinela.

Executa a coleta automatizada no Emissor Nacional (nfse.gov.br) para Serviços
Prestados (NFS-e Emitidas) e Serviços Tomados (NFS-e Recebidas), valida o CNPJ
da empresa logada no portal contra a Empresa Ativa do Sentinela, grava as
planilhas Excel identificadas, baixa XML e PDF nas pastas oficiais do cliente e
alimenta a apuração final do mês no banco de dados.
"""

from __future__ import annotations

import base64
import calendar
import gzip
import os
import random
import re
import shutil
import socket
import stat
import subprocess
import threading
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter

from clientes.competencia import deslocar, interpretar, rotulo
from clientes.models import Empresa, NotaServico
from clientes.utils_sharepoint import pasta_do_mes, pasta_relatorios

VERSAO_COLETOR = "1.4.5"

MESES_NOMES = [
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
]

MAX_DIAS_INCLUSIVOS = 30
PAUSA_ENTRE_NOTAS_MIN_MS = 8_000
PAUSA_ENTRE_NOTAS_MAX_MS = 15_000

URL_LOGIN = "https://www.nfse.gov.br/EmissorNacional/Login?ReturnUrl=%2femissornacional"
URL_DASHBOARD = "https://www.nfse.gov.br/EmissorNacional/Dashboard"
URL_RECEBIDAS_PADRAO = "https://www.nfse.gov.br/EmissorNacional/Notas/Recebidas"
URL_EMITIDAS_PADRAO = "https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas"

RE_VISUALIZAR = re.compile(r"^\s*Visualizar", re.I)
RE_FILTRAR = re.compile(r"^\s*Filtrar", re.I)
RE_SOBRECARGA = re.compile(
    r"consumo|sobrecarga|alto volume|tente novamente|indispon[ií]vel|"
    r"temporariamente|too many|aguarde alguns|sistema (está )?ocupado",
    re.I,
)

CABECALHO_TOMADOS = (
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
    "CNPJ Cliente (Sentinela)",
    "Empresa Cliente (Sentinela)",
)

CABECALHO_PRESTADOS = (
    "Número",
    "Chave de Acesso",
    "Data Emissão",
    "Competência de Emissão",
    "Razão do Tomador",
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
    "CNPJ Cliente (Sentinela)",
    "Empresa Cliente (Sentinela)",
)

COLS_VALOR = (7, 8, 10, 11, 12, 13)
COL_STATUS = 14
COL_DATA_CANCEL = 15

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date
    rotulo: str

    def fatias(self) -> list[tuple[date, date]]:
        partes: list[tuple[date, date]] = []
        atual = self.inicio
        while atual <= self.fim:
            ultimo = min(atual + timedelta(days=MAX_DIAS_INCLUSIVOS - 1), self.fim)
            partes.append((atual, ultimo))
            atual = ultimo + timedelta(days=1)
        return partes


def fmt_br(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def parse_data_br(texto: str) -> date:
    bruto = (texto or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(bruto, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Data inválida: {texto}")


def periodo_do_mes(mes: int, ano: int) -> Periodo:
    ultimo = calendar.monthrange(ano, mes)[1]
    inicio = date(ano, mes, 1)
    fim = date(ano, mes, ultimo)
    return Periodo(inicio=inicio, fim=fim, rotulo=f"{MESES_NOMES[mes - 1]} de {ano}")


def periodo_livre(data_inicial: str, data_final: str) -> Periodo:
    inicio = parse_data_br(data_inicial)
    fim = parse_data_br(data_final)
    if fim < inicio:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    return Periodo(
        inicio=inicio,
        fim=fim,
        rotulo=f"{inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
    )


def formatar_cnpj(cnpj: str) -> str:
    d = re.sub(r"\D", "", str(cnpj or ""))
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return d or str(cnpj or "")


def _decimal_br(valor) -> Decimal:
    if isinstance(valor, Decimal):
        return valor.quantize(Decimal("0.01"))
    if isinstance(valor, (int, float)):
        return Decimal(str(valor)).quantize(Decimal("0.01"))
    bruto = str(valor or "").strip()
    if not bruto:
        return Decimal("0.00")
    limpo = re.sub(r"[R$\s]", "", bruto, flags=re.I)
    if "," in limpo and "." in limpo:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif "," in limpo:
        limpo = limpo.replace(",", ".")
    try:
        return Decimal(limpo).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _parse_valor_excel(texto) -> float | str:
    if isinstance(texto, (int, float, Decimal)):
        return float(texto)
    bruto = str(texto or "").strip()
    if not bruto:
        return ""
    limpo = re.sub(r"[R$\s]", "", bruto, flags=re.I)
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return bruto


def _parse_data_celula(texto) -> datetime | str:
    if isinstance(texto, datetime):
        return texto
    if isinstance(texto, date):
        return datetime.combine(texto, datetime.min.time())
    bruto = str(texto or "").strip()
    if not bruto:
        return ""
    so_data = bruto.split()[0] if bruto else bruto
    try:
        return datetime.combine(parse_data_br(so_data), datetime.min.time())
    except ValueError:
        return bruto


def _parse_data_hora(texto) -> datetime | str:
    if isinstance(texto, datetime):
        return texto
    if isinstance(texto, date):
        return datetime.combine(texto, datetime.min.time())
    bruto = str(texto or "").strip()
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


def _normalizar_competencia_mm_aaaa(texto: str) -> str:
    """Converte '10/2026' ou '102026' para '102026'."""
    bruto = (texto or "").strip()
    m = re.search(r"(\d{2})\s*/\s*(\d{4})", bruto)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    digitos = re.sub(r"\D", "", bruto)
    if len(digitos) == 6:
        return digitos
    return ""


def diagnosticar_nota_servico(
    *,
    empresa: Empresa,
    competencia_sessao: str,
    tipo: str,
    numero: str,
    data_emissao: date | None,
    competencia_emissao: str,
    valor_servico: Decimal,
    valor_liquido: Decimal,
    retencao: str,
    valor_issqn: Decimal,
    contrib_sociais_retidas: Decimal,
    contrib_previdenciaria_retida: Decimal,
    irrf: Decimal,
    status: str,
    data_cancelamento: str,
    tem_xml: bool,
    etapa_atual: str = "",
) -> dict:
    """Aplica as regras fiscais do Sentinela para apuração final do mês e mês seguinte."""
    alertas: list[str] = []
    sugestoes: list[str] = []
    status_limpo = (status or "Ativa").strip()
    cancelada = status_limpo.lower() == "cancelada"

    comp_nota_mmaaaa = _normalizar_competencia_mm_aaaa(competencia_emissao)
    comp_emissao_mmaaaa = f"{data_emissao.month:02d}{data_emissao.year}" if data_emissao else ""
    proxima_competencia = deslocar(competencia_sessao, 1)

    etapa = etapa_atual if etapa_atual in {"ESCRITURADA", "MES_SEGUINTE"} else "PENDENTE"
    comp_escrituracao = competencia_sessao

    if cancelada:
        etapa = "NAO_ESCRITURAR"
        comp_escrituracao = competencia_sessao
        dt_canc = _parse_data_hora(data_cancelamento)
        if isinstance(dt_canc, datetime) and data_emissao:
            comp_canc = f"{dt_canc.month:02d}{dt_canc.year}"
            if comp_canc != comp_emissao_mmaaaa:
                alertas.append(
                    f"Cancelamento extemporâneo: emitida em {data_emissao:%d/%m/%Y} e cancelada em {data_cancelamento}."
                )
                sugestoes.append(
                    "Conferir se a nota foi escriturada no mês de emissão e se exige estorno/ajuste fiscal neste mês."
                )
        if not alertas:
            sugestoes.append("Nota cancelada: conferir se consta como cancelada na Domínio (sem gerar tributo).")
    else:
        # 1. Verificar divergência entre Data de Emissão e Competência de Emissão / Sessão
        if comp_nota_mmaaaa and comp_emissao_mmaaaa and comp_nota_mmaaaa != comp_emissao_mmaaaa:
            alertas.append(
                f"Data de emissão ({data_emissao:%d/%m/%Y}) em mês diferente da competência da nota ({competencia_emissao})."
            )
            if comp_emissao_mmaaaa == proxima_competencia or comp_nota_mmaaaa == proxima_competencia:
                if not etapa_atual:
                    etapa = "MES_SEGUINTE"
                comp_escrituracao = proxima_competencia
                sugestoes.append(
                    f"Transição de mês: verificar se ficará para escriturar na competência seguinte ({rotulo(proxima_competencia)})."
                )
            else:
                sugestoes.append(
                    "Conferir se já foi escriturada na competência de origem ou se deve entrar nesta apuração."
                )
        elif comp_emissao_mmaaaa and comp_emissao_mmaaaa != competencia_sessao:
            alertas.append(
                f"Nota emitida em {data_emissao:%d/%m/%Y} (fora da competência ativa {rotulo(competencia_sessao)})."
            )
            if comp_emissao_mmaaaa == proxima_competencia:
                if not etapa_atual:
                    etapa = "MES_SEGUINTE"
                comp_escrituracao = proxima_competencia
                sugestoes.append(
                    f"Nota emitida no mês seguinte: separada para escriturar em {rotulo(proxima_competencia)}."
                )
            else:
                sugestoes.append("Verificar se faltou escriturar no mês de emissão ou se entra neste fechamento.")

        if etapa == "MES_SEGUINTE":
            comp_escrituracao = proxima_competencia

        # 2. Verificar retenções federais e municipais
        ret_txt = (retencao or "").lower()
        iss_retido = ("retid" in ret_txt and "não" not in ret_txt and "nao" not in ret_txt) and valor_issqn > 0
        tem_retencao_federal = (contrib_sociais_retidas > 0) or (contrib_previdenciaria_retida > 0) or (irrf > 0)

        partes_ret = []
        if iss_retido:
            partes_ret.append(f"ISS Retido R$ {valor_issqn}")
        if irrf > 0:
            partes_ret.append(f"IRRF R$ {irrf}")
        if contrib_sociais_retidas > 0:
            partes_ret.append(f"CSRF (PIS/COFINS/CSLL) R$ {contrib_sociais_retidas}")
        if contrib_previdenciaria_retida > 0:
            partes_ret.append(f"INSS R$ {contrib_previdenciaria_retida}")

        if partes_ret:
            resumo_ret = ", ".join(partes_ret)
            if tipo == "PRESTADO":
                sugestoes.append(
                    f"Retenção sofrida ({resumo_ret}): conferir acumulador na Domínio para abater o imposto retido na apuração."
                )
            else:
                alertas.append(f"Serviço tomado com retenção ({resumo_ret}).")
                sugestoes.append(
                    "Conferir lançamento das retenções na Domínio (EFD-Reinf / DARF / ISS Retido) ou se o pagamento/escrituração ficará para o mês seguinte."
                )

        # 3. Leitura parcial (sem detalhe / sem XML)
        if valor_servico > 0 and valor_liquido == 0:
            alertas.append("Nota lida apenas pela listagem do portal (detalhe de retenções não abriu).")
            sugestoes.append("Conferir nota no portal ou XML na Domínio para validar possíveis retenções.")
        elif not tem_xml:
            sugestoes.append("Escriturar e conferir na Domínio.")

    return {
        "etapa_escrituracao": etapa,
        "competencia_escrituracao": comp_escrituracao,
        "possui_alerta": len(alertas) > 0,
        "motivo_alerta": " ".join(alertas),
        "sugestao_acao": " ".join(sugestoes) if sugestoes else "Pronta para conferência na apuração do mês.",
    }


def salvar_notas_no_banco(
    *,
    empresa: Empresa,
    competencia: str,
    tipo_grupo: str,
    notas: list[dict],
    docs: dict | None = None,
) -> int:
    """Salva ou atualiza as notas coletadas ou importadas no banco do Sentinela."""
    tipo_db = "PRESTADO" if tipo_grupo == "prestados" else "TOMADO"
    mapa_docs = docs or {}
    gravadas = 0

    for item in notas:
        chave_bruta = str(item.get("chave_acesso") or "").strip()
        chave_digitos = re.sub(r"\D", "", chave_bruta)
        numero = str(item.get("numero") or "").strip()
        if not chave_digitos and not numero:
            continue
        chave_unica = chave_digitos or f"SEMCHAVE-{tipo_db}-{competencia}-{numero}"

        dt_emissao = None
        txt_emissao = str(item.get("data_emissao") or "").strip()
        if isinstance(item.get("data_emissao"), (date, datetime)):
            d_obj = item["data_emissao"]
            dt_emissao = d_obj.date() if isinstance(d_obj, datetime) else d_obj
            txt_emissao = dt_emissao.strftime("%d/%m/%Y")
        elif txt_emissao:
            try:
                dt_emissao = parse_data_br(txt_emissao.split()[0])
            except ValueError:
                dt_emissao = None

        comp_emissao = str(item.get("competencia_emissao") or "").strip()
        if not comp_emissao and dt_emissao:
            comp_emissao = f"{dt_emissao.month:02d}/{dt_emissao.year}"

        razao = str(
            item.get("razao_emitente")
            or item.get("razao_tomador")
            or item.get("razao_terceiro")
            or ""
        ).strip()

        v_servico = _decimal_br(item.get("valor_servico"))
        v_liquido = _decimal_br(item.get("valor_liquido"))
        retencao = str(item.get("retencao") or "").strip()
        v_iss = _decimal_br(item.get("valor_issqn"))
        v_cs = _decimal_br(item.get("contrib_sociais_retidas"))
        v_inss = _decimal_br(item.get("contrib_previdenciaria_retida"))
        v_irrf = _decimal_br(item.get("irrf"))
        status = str(item.get("status") or "Ativa").strip() or "Ativa"
        dt_canc = str(item.get("data_cancelamento") or "").strip()

        doc_info = mapa_docs.get(chave_digitos) or {}
        caminho_xml = str(doc_info.get("xml") or item.get("caminho_xml") or "")
        caminho_pdf = str(doc_info.get("pdf") or item.get("caminho_pdf") or "")
        tem_xml = bool(caminho_xml and Path(caminho_xml).is_file())
        tem_pdf = bool(caminho_pdf and Path(caminho_pdf).is_file())

        existente = NotaServico.objects.filter(
            empresa=empresa,
            tipo=tipo_db,
            chave_acesso=chave_unica,
        ).first()
        etapa_anterior = existente.etapa_escrituracao if existente else ""

        diag = diagnosticar_nota_servico(
            empresa=empresa,
            competencia_sessao=competencia,
            tipo=tipo_db,
            numero=numero,
            data_emissao=dt_emissao,
            competencia_emissao=comp_emissao,
            valor_servico=v_servico,
            valor_liquido=v_liquido,
            retencao=retencao,
            valor_issqn=v_iss,
            contrib_sociais_retidas=v_cs,
            contrib_previdenciaria_retida=v_inss,
            irrf=v_irrf,
            status=status,
            data_cancelamento=dt_canc,
            tem_xml=tem_xml or (existente.tem_xml if existente else False),
            etapa_atual=etapa_anterior,
        )

        NotaServico.objects.update_or_create(
            empresa=empresa,
            tipo=tipo_db,
            chave_acesso=chave_unica,
            defaults={
                "competencia": competencia,
                "numero": numero[:30],
                "data_emissao": dt_emissao,
                "data_emissao_texto": txt_emissao[:30],
                "competencia_emissao": comp_emissao[:10],
                "razao_terceiro": razao[:255],
                "codigo_tributacao_nacional": str(item.get("codigo_tributacao_nacional") or "").strip()[:255],
                "valor_servico": v_servico,
                "valor_liquido": v_liquido,
                "retencao": retencao[:80],
                "valor_issqn": v_iss,
                "contrib_sociais_retidas": v_cs,
                "contrib_previdenciaria_retida": v_inss,
                "irrf": v_irrf,
                "status": status[:30],
                "data_cancelamento": dt_canc[:40],
                "tem_xml": tem_xml or (existente.tem_xml if existente else False),
                "tem_pdf": tem_pdf or (existente.tem_pdf if existente else False),
                "caminho_xml": caminho_xml or (existente.caminho_xml if existente else ""),
                "caminho_pdf": caminho_pdf or (existente.caminho_pdf if existente else ""),
                "etapa_escrituracao": diag["etapa_escrituracao"],
                "competencia_escrituracao": diag["competencia_escrituracao"],
                "possui_alerta": diag["possui_alerta"],
                "motivo_alerta": diag["motivo_alerta"],
                "sugestao_acao": diag["sugestao_acao"],
            },
        )
        gravadas += 1

    return gravadas


def gravar_planilha_empresa(
    notas: list[dict],
    destino: Path,
    *,
    rotulo_periodo: str,
    tipo: str,
    empresa: Empresa,
) -> Path:
    """Gera a planilha Excel do Coletor 1.4.5 com a identificação completa da Empresa."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    prestado = tipo == "prestados"
    titulo_aba = "NFS-e Prestadas" if prestado else "NFS-e Recebidas"
    cabecalho = CABECALHO_PRESTADOS if prestado else CABECALHO_TOMADOS

    wb = Workbook()
    ws = wb.active
    ws.title = titulo_aba

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

    cnpj_fmt = formatar_cnpj(empresa.cnpj)
    identificacao_cliente = f"{empresa.codigo_dominio} - {empresa.razao_social} ({cnpj_fmt})"

    ws.merge_cells("A1:F1")
    ws["A1"] = f"{titulo_aba} — {rotulo_periodo}  |  Cliente: {identificacao_cliente}"
    ws["A1"].font = Font(name="Calibri", bold=True, size=11, color="0F766E")
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

    for col, titulo in enumerate(cabecalho, start=1):
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
            (nota.get("razao_emitente") or nota.get("razao_terceiro") or "").strip(),
            (nota.get("codigo_tributacao_nacional") or "").strip(),
            _parse_valor_excel(nota.get("valor_servico") or ""),
            _parse_valor_excel(nota.get("valor_liquido") or ""),
            (nota.get("retencao") or "").strip(),
            _parse_valor_excel(nota.get("valor_issqn") or ""),
            _parse_valor_excel(nota.get("contrib_sociais_retidas") or ""),
            _parse_valor_excel(nota.get("contrib_previdenciaria_retida") or ""),
            _parse_valor_excel(nota.get("irrf") or ""),
            (nota.get("status") or "Ativa").strip() or "Ativa",
            _parse_data_hora(nota.get("data_cancelamento") or ""),
            cnpj_fmt,
            f"{empresa.codigo_dominio} - {empresa.razao_social}",
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
                cel.number_format = (
                    "DD/MM/YYYY HH:MM:SS"
                    if (valor.hour or valor.minute or valor.second)
                    else "DD/MM/YYYY"
                )
                cel.alignment = Alignment(horizontal="center")
            elif col in COLS_VALOR and isinstance(valor, float):
                cel.number_format = '"R$" #,##0.00'
                cel.alignment = Alignment(horizontal="right")
            elif col in (1, COL_STATUS, 16):
                cel.alignment = Alignment(horizontal="center")
            elif col == 6:
                cel.alignment = Alignment(wrap_text=True, vertical="top")

    larguras = [18, 32, 16, 18, 52, 68, 18, 18, 26, 18, 28, 32, 16, 14, 22, 22, 42]
    for i, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = largura

    ultima_filtro = max(2, 2 + len(notas))
    ultima_letra = get_column_letter(len(cabecalho))
    ws.auto_filter.ref = f"A2:{ultima_letra}{ultima_filtro}"
    ws.freeze_panes = "A3"
    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 32

    solta = Protection(locked=False, hidden=False)
    ws.protection.sheet = False
    ws.protection.disable()
    for row in ws.iter_rows(min_row=1, max_row=ultima_filtro, max_col=len(cabecalho)):
        for cell in row:
            cell.protection = solta

    wb.save(destino)
    try:
        os.chmod(destino, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass
    return destino


def importar_planilha_coletor(empresa: Empresa, competencia: str, arquivo_bytes: bytes, nome_arquivo: str, tipo_forcado: str = "") -> dict:
    """Lê uma planilha .xlsx gerada pelo Coletor 1.4.5 (ou anterior) e grava no cliente ativo."""
    import io

    wb = load_workbook(io.BytesIO(arquivo_bytes), data_only=True)
    ws = wb.active
    titulo_a1 = str(ws["A1"].value or "")
    nome_baixo = (nome_arquivo or "").lower()

    # Verificar se a planilha já tem CNPJ identificado de outra empresa
    m_cnpj = re.search(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}|\d{14})\b", titulo_a1)
    if m_cnpj:
        cnpj_planilha = re.sub(r"\D", "", m_cnpj.group(1))
        if len(cnpj_planilha) == 14 and cnpj_planilha != empresa.cnpj:
            return {
                "ok": False,
                "erro": (
                    f"Esta planilha pertence ao CNPJ {formatar_cnpj(cnpj_planilha)}, "
                    f"mas a empresa ativa no Sentinela é {empresa.codigo_dominio} · {empresa.razao_social} ({formatar_cnpj(empresa.cnpj)})."
                ),
            }

    cabecalhos = [str(ws.cell(2, c).value or "").strip() for c in range(1, ws.max_column + 1)]
    cab_baixo = " ".join(cabecalhos).lower()

    if tipo_forcado in ("prestados", "tomados"):
        tipo = tipo_forcado
    elif "tomador" in cab_baixo or "prestadas" in titulo_a1.lower() or "prestadas" in nome_baixo or "prestados" in nome_baixo:
        tipo = "prestados"
    else:
        tipo = "tomados"

    notas = []
    for r in range(3, (ws.max_row or 2) + 1):
        num = ws.cell(r, 1).value
        chave = ws.cell(r, 2).value
        if not num and not chave:
            continue
        dt_val = ws.cell(r, 3).value
        if isinstance(dt_val, (datetime, date)):
            dt_str = dt_val.strftime("%d/%m/%Y")
        else:
            dt_str = str(dt_val or "").strip()

        canc_val = ws.cell(r, 15).value
        if isinstance(canc_val, datetime):
            canc_str = canc_val.strftime("%d/%m/%Y %H:%M:%S") if (canc_val.hour or canc_val.minute) else canc_val.strftime("%d/%m/%Y")
        elif isinstance(canc_val, date):
            canc_str = canc_val.strftime("%d/%m/%Y")
        else:
            canc_str = str(canc_val or "").strip()

        notas.append({
            "numero": str(num or "").strip(),
            "chave_acesso": str(chave or "").strip(),
            "data_emissao": dt_str,
            "competencia_emissao": str(ws.cell(r, 4).value or "").strip(),
            "razao_emitente": str(ws.cell(r, 5).value or "").strip(),
            "codigo_tributacao_nacional": str(ws.cell(r, 6).value or "").strip(),
            "valor_servico": ws.cell(r, 7).value,
            "valor_liquido": ws.cell(r, 8).value,
            "retencao": str(ws.cell(r, 9).value or "").strip(),
            "valor_issqn": ws.cell(r, 10).value,
            "contrib_sociais_retidas": ws.cell(r, 11).value,
            "contrib_previdenciaria_retida": ws.cell(r, 12).value,
            "irrf": ws.cell(r, 13).value,
            "status": str(ws.cell(r, 14).value or "Ativa").strip() or "Ativa",
            "data_cancelamento": canc_str,
        })

    if not notas:
        return {"ok": False, "erro": "A planilha enviada não possui linhas de NFS-e para importar."}

    gravadas = salvar_notas_no_banco(
        empresa=empresa,
        competencia=competencia,
        tipo_grupo=tipo,
        notas=notas,
    )

    # Salvar a planilha identificada nas pastas oficiais do cliente no Sentinela
    partes = interpretar(competencia)
    rotulo_per = rotulo(competencia) if partes else competencia
    _salvar_arquivos_no_cliente(
        empresa=empresa,
        competencia=competencia,
        tipo=tipo,
        notas=notas,
        docs={},
        rotulo_periodo=rotulo_per,
        log=lambda _m: None,
    )
    return {
        "ok": True,
        "tipo": tipo,
        "gravadas": gravadas,
    }


def importar_xml_nfse(empresa: Empresa, competencia: str, caminho_xml: Path) -> dict | None:
    """Lê um XML de NFS-e Padrão Nacional (ou municipal) e vincula ao cliente se pertencer a ele."""
    try:
        root = ET.parse(caminho_xml).getroot()
    except Exception:
        return None
    tag_raiz = root.tag.rsplit("}", 1)[-1].lower()
    if "nfe" in tag_raiz and "nfse" not in tag_raiz:
        return None

    def _txt(pai, *nomes):
        if pai is None:
            return ""
        for item in pai.iter():
            t = item.tag.rsplit("}", 1)[-1].lower()
            if t in nomes and item.text:
                return item.text.strip()
        return ""

    def _bloco(pai, *nomes):
        if pai is None:
            return None
        for item in pai.iter():
            t = item.tag.rsplit("}", 1)[-1].lower()
            if t in nomes:
                return item
        return None

    emit = _bloco(root, "emit", "prestador", "prestadorservico")
    toma = _bloco(root, "toma", "tomador", "tomadorservico")
    cnpj_emit = re.sub(r"\D", "", _txt(emit, "cnpj", "cpf"))
    cnpj_toma = re.sub(r"\D", "", _txt(toma, "cnpj", "cpf"))
    nome_emit = _txt(emit, "xnome", "razaosocial", "nome")
    nome_toma = _txt(toma, "xnome", "razaosocial", "nome")

    if cnpj_emit == empresa.cnpj:
        tipo = "prestados"
        razao_terceiro = nome_toma
    elif cnpj_toma == empresa.cnpj:
        tipo = "tomados"
        razao_terceiro = nome_emit
    else:
        return None

    chave = ""
    for item in root.iter():
        for attr_val in item.attrib.values():
            dig = re.sub(r"\D", "", str(attr_val))
            if len(dig) >= 44:
                chave = dig
                break
        if chave:
            break

    numero = _txt(root, "nnfse", "ndps", "numero")
    dh_emi = _txt(root, "dhemi", "dataemissao", "demi")
    dt_str = ""
    if len(dh_emi) >= 10 and dh_emi[4] == "-" and dh_emi[7] == "-":
        dt_str = f"{dh_emi[8:10]}/{dh_emi[5:7]}/{dh_emi[:4]}"
    d_comp = _txt(root, "dcompet", "competencia")
    comp_str = ""
    if len(d_comp) >= 7 and d_comp[4] == "-":
        comp_str = f"{d_comp[5:7]}/{d_comp[:4]}"

    valores_el = _bloco(root, "valores", "vservprest", "valoresnfse")
    v_serv = _txt(valores_el or root, "vserv", "valorservicos")
    v_liq = _txt(valores_el or root, "vliq", "valorliquidonfse")
    v_iss = _txt(valores_el or root, "viss", "valoriss")
    v_irrf = _txt(valores_el or root, "virrf", "valorir")
    v_csll = _txt(valores_el or root, "vcsll", "vretcsll")
    v_pis = _txt(valores_el or root, "vpis", "vretpis")
    v_cofins = _txt(valores_el or root, "vcofins", "vretcofins")
    v_inss = _txt(valores_el or root, "vinss", "vretcp")
    c_trib = _txt(root, "ctribnac", "itemlistaservico")

    cs_total = _decimal_br(v_csll) + _decimal_br(v_pis) + _decimal_br(v_cofins)

    pasta_mes = pasta_do_mes(empresa, competencia)
    sub_nfse = (
        "02_XML_PROCESSADOS_SENTINELA/03_NFSE_PADRAO_NACIONAL/01_SERVICOS_PRESTADOS/xml"
        if tipo == "prestados"
        else "02_XML_PROCESSADOS_SENTINELA/03_NFSE_PADRAO_NACIONAL/02_SERVICOS_TOMADOS/xml"
    )
    pasta_xml_dest = pasta_mes / sub_nfse
    pasta_xml_dest.mkdir(parents=True, exist_ok=True)
    dest_xml = pasta_xml_dest / caminho_xml.name
    if caminho_xml.resolve() != dest_xml.resolve():
        shutil.copy2(caminho_xml, dest_xml)

    nota_dict = {
        "numero": numero,
        "chave_acesso": chave or f"XML-{tipo}-{numero}-{dt_str}",
        "data_emissao": dt_str,
        "competencia_emissao": comp_str,
        "razao_emitente": razao_terceiro,
        "codigo_tributacao_nacional": c_trib,
        "valor_servico": v_serv,
        "valor_liquido": v_liq or v_serv,
        "retencao": "ISS Retido" if _decimal_br(v_iss) > 0 and _decimal_br(v_liq) < _decimal_br(v_serv) else "",
        "valor_issqn": v_iss,
        "contrib_sociais_retidas": str(cs_total) if cs_total > 0 else "",
        "contrib_previdenciaria_retida": v_inss,
        "irrf": v_irrf,
        "status": "Ativa",
        "data_cancelamento": "",
        "caminho_xml": str(dest_xml),
    }
    salvar_notas_no_banco(
        empresa=empresa,
        competencia=competencia,
        tipo_grupo=tipo,
        notas=[nota_dict],
    )
    return nota_dict


def _salvar_arquivos_no_cliente(
    *,
    empresa: Empresa,
    competencia: str,
    tipo: str,
    notas: list[dict],
    docs: dict,
    rotulo_periodo: str,
    log: LogFn,
) -> Path | None:
    """Salva planilha, XMLs e PDFs nas pastas oficiais do cliente no Sentinela."""
    if not notas:
        return None
    pasta_mes = pasta_do_mes(empresa, competencia)
    pasta_rel = pasta_relatorios(empresa, competencia)

    prestado = tipo == "prestados"
    sub_nfse = (
        pasta_mes / "02_XML_PROCESSADOS_SENTINELA" / "03_NFSE_PADRAO_NACIONAL" / "01_SERVICOS_PRESTADOS"
        if prestado
        else pasta_mes / "02_XML_PROCESSADOS_SENTINELA" / "03_NFSE_PADRAO_NACIONAL" / "02_SERVICOS_TOMADOS"
    )
    sub_nfse.mkdir(parents=True, exist_ok=True)

    nome_padrao = (
        "Relatório NFS-e Prestadas - Portal Nacional.xlsx"
        if prestado
        else "Relatório NFS-e Tomadas - Portal Nacional.xlsx"
    )
    nome_identificado = (
        f"Relatório NFS-e {'Prestadas' if prestado else 'Tomadas'} - "
        f"{str(empresa.codigo_dominio).zfill(4)}_{empresa.cnpj}_{competencia}.xlsx"
    )

    caminho_sub = sub_nfse / nome_padrao
    caminho_rel = pasta_rel / nome_identificado

    gravar_planilha_empresa(notas, caminho_sub, rotulo_periodo=rotulo_periodo, tipo=tipo, empresa=empresa)
    shutil.copy2(caminho_sub, caminho_rel)

    # Copiar XMLs e PDFs baixados para a pasta da empresa/mês
    quantos = {"xml": 0, "pdf": 0}
    for chave, item in (docs or {}).items():
        for ext in ("xml", "pdf"):
            origem = item.get(ext)
            if not origem:
                continue
            origem_p = Path(origem)
            if not origem_p.is_file():
                continue
            pasta_ext = sub_nfse / ext
            pasta_ext.mkdir(parents=True, exist_ok=True)
            alvo = pasta_ext / origem_p.name
            if alvo.resolve() != origem_p.resolve():
                shutil.copy2(origem_p, alvo)
            item[ext] = alvo
            quantos[ext] += 1

    log(
        f"Relatório salvo no cliente ({empresa.codigo_dominio} · {empresa.razao_social}): "
        f"{caminho_rel.name} (XML: {quantos['xml']}, PDF: {quantos['pdf']})."
    )
    return caminho_rel


# ============================================================================
# Detecção de CNPJ Logado no Portal Nacional (Segurança Multi-Empresa)
# ============================================================================

_JS_DETECTAR_CONTRIBUINTE_PORTAL = r"""
() => {
  const zonas = [
    ...document.querySelectorAll("header, .navbar, nav, .user-info, .usuario, .contribuinte, .perfil, .top-bar, .dropdown-toggle")
  ];
  const textos = zonas.map((z) => (z.innerText || z.textContent || "").replace(/\s+/g, " ").trim());
  const corpo = (document.body && document.body.innerText) ? document.body.innerText.slice(0, 3500) : "";
  const junto = textos.join(" | ") + " | " + corpo;
  const encontrados = [];
  const reFormatado = /\b(\d{2}\.\d{3}\.\d{3}\/\d{4}-\d{2})\b/g;
  let m;
  while ((m = reFormatado.exec(junto)) !== null) {
    const limpo = m[1].replace(/\D/g, "");
    if (!encontrados.includes(limpo)) encontrados.push(limpo);
  }
  return encontrados;
}
"""


def _conferir_cnpj_portal(page, empresa: Empresa, log: LogFn) -> None:
    """Confere se o CNPJ logado no Portal Nacional bate com a Empresa Ativa do Sentinela."""
    try:
        cnpjs = page.evaluate(_JS_DETECTAR_CONTRIBUINTE_PORTAL) or []
    except Exception:
        cnpjs = []
    if not cnpjs:
        return
    cnpj_alvo = re.sub(r"\D", "", empresa.cnpj or "")
    if cnpj_alvo in cnpjs:
        log(
            f"CNPJ confirmado no Portal Nacional: {formatar_cnpj(cnpj_alvo)} "
            f"({empresa.codigo_dominio} · {empresa.razao_social})."
        )
        return
    # Se encontrou exatamente um CNPJ no cabeçalho do portal e é diferente do cliente ativo:
    primeiro = cnpjs[0]
    if len(primeiro) == 14 and primeiro != cnpj_alvo:
        raise RuntimeError(
            f"ATENÇÃO: O login no Portal Nacional foi feito com o CNPJ {formatar_cnpj(primeiro)}, "
            f"mas a empresa selecionada no Sentinela é {empresa.codigo_dominio} · {empresa.razao_social} "
            f"(CNPJ {formatar_cnpj(cnpj_alvo)}). Troque a empresa ativa no Sentinela ou entre com o "
            f"certificado/login correto para não misturar os relatórios!"
        )


# ============================================================================
# Gerenciador de Execução do Coletor 1.4.5 pelo Sentinela Web
# ============================================================================

class GerenciadorColetorSentinela:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.rodando = False
        self.parar_evento = threading.Event()
        self.confirmar_evento = threading.Event()
        self.logs: list[str] = []
        self.ultimo_status: str = f"Apanhador NFS-e v{VERSAO_COLETOR} pronto."
        self.erro: str = ""
        self.concluido: bool = False
        self.empresa_id: int | None = None
        self.empresa_nome: str = ""
        self.empresa_cnpj: str = ""
        self.competencia: str = ""
        self.total_prestados: int = 0
        self.total_tomados: int = 0

    def escrever_log(self, msg: str) -> None:
        if "30 dias" in msg or msg.startswith("Parte ") or msg.startswith("Nenhuma nota neste período"):
            return
        linha = f"{datetime.now():%H:%M:%S}  {msg}"
        with self._lock:
            self.logs.append(linha)
            if len(self.logs) > 400:
                self.logs = self.logs[-400:]
            self.ultimo_status = msg[:200]

    def estado_atual(self) -> dict:
        with self._lock:
            return {
                "versao": VERSAO_COLETOR,
                "rodando": self.rodando,
                "concluido": self.concluido,
                "erro": self.erro,
                "ultimo_status": self.ultimo_status,
                "logs": list(self.logs[-120:]),
                "empresa_id": self.empresa_id,
                "empresa_nome": self.empresa_nome,
                "empresa_cnpj": self.empresa_cnpj,
                "competencia": self.competencia,
                "total_prestados": self.total_prestados,
                "total_tomados": self.total_tomados,
            }

    def pedir_parar(self) -> None:
        self.parar_evento.set()
        self.escrever_log("Parando… aguarde a conclusão da nota atual.")

    def iniciar_coleta(
        self,
        *,
        empresa: Empresa,
        competencia: str,
        periodo: Periodo,
        usuario=None,
    ) -> tuple[bool, str]:
        with self._lock:
            if self.rodando:
                return False, f"O Apanhador já está em execução para {self.empresa_nome}."
            self.rodando = True
            self.concluido = False
            self.erro = ""
            self.logs = []
            self.parar_evento.clear()
            self.confirmar_evento.clear()
            self.empresa_id = empresa.pk
            self.empresa_nome = f"{empresa.codigo_dominio} · {empresa.razao_social}"
            self.empresa_cnpj = formatar_cnpj(empresa.cnpj)
            self.competencia = competencia
            self.total_prestados = 0
            self.total_tomados = 0

        self.escrever_log(
            f"Apanhador NFS-e v{VERSAO_COLETOR} iniciado para {empresa.codigo_dominio} · {empresa.razao_social} "
            f"(CNPJ {formatar_cnpj(empresa.cnpj)}) — Competência {rotulo(competencia)} "
            f"({periodo.inicio:%d/%m/%Y} a {periodo.fim:%d/%m/%Y})."
        )
        t = threading.Thread(
            target=self._executar_thread,
            args=(empresa.pk, competencia, periodo, getattr(usuario, "pk", None)),
            daemon=True,
        )
        t.start()
        return True, "Apanhador 1.4.5 iniciado! O Google Chrome abrirá na tela de login do Portal Nacional."

    def _executar_thread(self, empresa_id: int, competencia: str, periodo: Periodo, usuario_id: int | None) -> None:
        from django.contrib.auth import get_user_model
        from clientes.trilha import registrar

        empresa = Empresa.objects.filter(pk=empresa_id).first()
        if empresa is None:
            with self._lock:
                self.rodando = False
                self.erro = "Empresa não encontrada."
            return

        # Prepara os módulos originais do Coletor 1.4.5
        pasta_base_coletor = Path(r"D:\App Mari\ColetorNovo")
        pasta_dados_exec = pasta_do_mes(empresa, competencia) / "03_ARQUIVOS_DOMINIO" / "coletor_dados"
        pasta_dados_exec.mkdir(parents=True, exist_ok=True)

        try:
            grupos = _rodar_motor_145(
                empresa=empresa,
                competencia=competencia,
                periodo=periodo,
                pasta_dados=pasta_dados_exec,
                pasta_coletor_novo=pasta_base_coletor,
                parar=self.parar_evento,
                confirmar_login=self.confirmar_evento,
                log=self.escrever_log,
            )
            tot_p = 0
            tot_t = 0
            for g in grupos:
                notas = g.get("notas") or []
                docs = g.get("docs") or {}
                tipo = g.get("tipo") or "tomados"
                if notas:
                    _salvar_arquivos_no_cliente(
                        empresa=empresa,
                        competencia=competencia,
                        tipo=tipo,
                        notas=notas,
                        docs=docs,
                        rotulo_periodo=periodo.rotulo,
                        log=self.escrever_log,
                    )
                    gravadas = salvar_notas_no_banco(
                        empresa=empresa,
                        competencia=competencia,
                        tipo_grupo=tipo,
                        notas=notas,
                        docs=docs,
                    )
                    if tipo == "prestados":
                        tot_p += gravadas
                    else:
                        tot_t += gravadas

            with self._lock:
                self.total_prestados = tot_p
                self.total_tomados = tot_t
                self.concluido = True

            self.escrever_log(
                f"Finalizado! {tot_p} NFS-e Prestada(s) e {tot_t} NFS-e Tomada(s) armazenadas "
                f"no cliente {empresa.codigo_dominio} · {empresa.razao_social} e prontas para a apuração do mês."
            )
            try:
                User = get_user_model()
                usr = User.objects.filter(pk=usuario_id).first() if usuario_id else None
                registrar(
                    usr,
                    empresa,
                    "COLETOR",
                    f"Apanhador NFS-e v{VERSAO_COLETOR}",
                    "",
                    f"Prestadas: {tot_p} | Tomadas: {tot_t} na competência {competencia}",
                )
            except Exception:
                pass
        except Exception as exc:
            msg = str(exc)
            self.escrever_log(f"Erro na coleta: {msg}")
            with self._lock:
                self.erro = msg
        finally:
            with self._lock:
                self.rodando = False


GERENCIADOR_COLETOR = GerenciadorColetorSentinela()


def _carregar_modulos_coletor_145(pasta_coletor_novo: Path | None = None):
    """Carrega os módulos do Coletor 1.4.5 embutidos no Sentinela (triagem_xml/coletor_145)."""
    import sys
    import types

    if "app.portal" in sys.modules and getattr(sys.modules["app.portal"], "_sentinela_145", False):
        return sys.modules["app.portal"], sys.modules["app.ui"]

    pasta_embutida = Path(__file__).resolve().parent / "coletor_145"
    if not pasta_embutida.is_dir():
        raise RuntimeError(f"Pasta do Coletor 1.4.5 não encontrada em {pasta_embutida}.")

    # Garantir stub leve caso tkinter não esteja no runtime embutido web
    if "tkinter" not in sys.modules:
        tk_stub = types.ModuleType("tkinter")
        tk_stub.Tk = object
        tk_stub.filedialog = types.ModuleType("tkinter.filedialog")
        tk_stub.messagebox = types.ModuleType("tkinter.messagebox")
        tk_stub.ttk = types.ModuleType("tkinter.ttk")
        sys.modules["tkinter"] = tk_stub
        sys.modules["tkinter.filedialog"] = tk_stub.filedialog
        sys.modules["tkinter.messagebox"] = tk_stub.messagebox
        sys.modules["tkinter.ttk"] = tk_stub.ttk

    for nome_mod, arquivo in (
        ("app", "__init__.py"),
        ("app.version", "version.py"),
        ("app.bootstrap", "bootstrap.py"),
        ("app.periodo", "periodo.py"),
        ("app.excel", "excel.py"),
        ("app.portal", "portal.py"),
    ):
        caminho = pasta_embutida / arquivo
        codigo = compile(caminho.read_text(encoding="utf-8"), str(caminho), "exec")
        mod = types.ModuleType(nome_mod)
        mod.__file__ = str(caminho)
        if nome_mod == "app":
            mod.__path__ = [str(pasta_embutida)]
        sys.modules[nome_mod] = mod
        exec(codigo, mod.__dict__)

    portal_mod = sys.modules["app.portal"]
    # Aplicar ganchos da versão 1.4.5 (ganchos.py)
    caminho_ganchos = pasta_embutida / "ganchos.py"
    if pasta_coletor_novo and (pasta_coletor_novo / "src" / "ganchos.py").is_file():
        caminho_ganchos = pasta_coletor_novo / "src" / "ganchos.py"
    ganchos_src = caminho_ganchos.read_text(encoding="utf-8")
    exec(compile(ganchos_src, str(caminho_ganchos), "exec"), portal_mod.__dict__)

    # Aplicar UI/funções 1.4.5 (ui.py)
    caminho_ui = pasta_embutida / "ui.py"
    if pasta_coletor_novo and (pasta_coletor_novo / "src" / "app" / "ui.py").is_file():
        caminho_ui = pasta_coletor_novo / "src" / "app" / "ui.py"
    ui_src = caminho_ui.read_text(encoding="utf-8")
    ui_mod = types.ModuleType("app.ui")
    ui_mod.__file__ = str(caminho_ui)
    sys.modules["app.ui"] = ui_mod
    exec(compile(ui_src, ui_mod.__file__, "exec"), ui_mod.__dict__)

    portal_mod._sentinela_145 = True
    return portal_mod, ui_mod


def _rodar_motor_145(
    *,
    empresa: Empresa,
    competencia: str,
    periodo: Periodo,
    pasta_dados: Path,
    pasta_coletor_novo: Path,
    parar: threading.Event,
    confirmar_login: threading.Event,
    log: LogFn,
) -> list[dict]:
    """Executa o fluxo completo do Coletor 1.4.5 vinculado à Empresa Ativa do Sentinela."""
    from playwright.sync_api import sync_playwright

    portal, ui = _carregar_modulos_coletor_145(pasta_coletor_novo)
    ui._instalar_login_direto()
    ui._instalar_retomada(parar, confirmar_login)

    log(f"Abrindo o Google Chrome no Emissor Nacional para {empresa.codigo_dominio} · {empresa.razao_social}…")
    playwright = sync_playwright().start()
    ctx = None
    grupos: list[dict] = []
    try:
        ctx = portal.abrir_navegador(playwright, pasta_dados)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        portal._forcar_tela_login(page, log)
        ui.aplicar_tipo("prestados")
        page = portal.aguardar_login(page, parar, confirmar_login, log)

        # Valida se o CNPJ logado no Portal Nacional pertence à empresa ativa do Sentinela!
        _conferir_cnpj_portal(page, empresa, log)

        log("A coleta começa pelos serviços prestados (NFS-e Emitidas) e depois busca os serviços tomados.")
        falha = None
        for tipo, titulo, pasta_nome in (
            ("prestados", "Serviços prestados", "Prestados"),
            ("tomados", "Serviços tomados", "Tomados"),
        ):
            if parar.is_set():
                break
            ui.aplicar_tipo(tipo)
            ui._preparar_documentos(pasta_dados, tipo)
            log(f"Iniciando {titulo}…")
            notas: list[dict] = []
            try:
                page = portal._ir_para_recebidas(page, log)
                _conferir_cnpj_portal(page, empresa, log)
                for ini, fim in periodo.fatias():
                    if parar.is_set():
                        break
                    page = portal._ir_para_recebidas(page, log)
                    novas = portal._coletar_periodo(page, ini, fim, parar, log)
                    notas.extend(novas)
                    try:
                        ui._baixar_documentos(page, novas, parar, log)
                    except Exception as falha_arq:
                        log("Aviso ao baixar XML/PDF desta parte: " + str(falha_arq).split("\n", 1)[0])
            except InterruptedError:
                grupos.append({
                    "tipo": tipo,
                    "titulo": titulo,
                    "pasta": pasta_nome,
                    "notas": notas,
                    "docs": dict(getattr(portal, "_DOCUMENTOS", {}) or {}),
                })
                log("Coleta interrompida pelo usuário.")
                return grupos
            except RuntimeError as err_rt:
                if "ATENÇÃO: O login no Portal Nacional" in str(err_rt):
                    raise
                falha = err_rt
                log(f"{titulo}: {str(err_rt).splitlines()[0]}")
            except Exception as exc:
                falha = exc
                log(f"{titulo}: {str(exc).splitlines()[0]}")

            grupos.append({
                "tipo": tipo,
                "titulo": titulo,
                "pasta": pasta_nome,
                "notas": notas,
                "docs": dict(getattr(portal, "_DOCUMENTOS", {}) or {}),
            })
        if not any(g["notas"] for g in grupos) and falha is not None:
            raise falha
        return grupos
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        try:
            playwright.stop()
        except Exception:
            pass
