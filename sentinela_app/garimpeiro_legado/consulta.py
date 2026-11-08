"""Painel do que a triagem já leu: listas, buracos e extração."""
from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict

from clientes.models import DocumentoXML
from clientes.utils_sharepoint import pasta_do_mes

ABAS_TELA = (
    ("lidos", "O que foi lido"),
    ("series", "Séries"),
    ("autorizadas", "Autorizadas"),
    ("canceladas", "Canceladas"),
    ("inutilizadas", "Inutilizadas"),
    ("denegadas", "Denegadas"),
    ("rejeitadas", "Rejeitadas"),
    ("buracos", "Buracos"),
)
ABAS = (
    "lidos",
    "autorizadas",
    "canceladas",
    "inutilizadas",
    "denegadas",
    "rejeitadas",
    "buracos",
    "series",
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
LIMITE_TELA = 400


def _vazio(mensagem: str = "") -> dict:
    return {
        "sem_arquivo": True,
        "erro": mensagem,
        "resumo": {"total": 0, "propria": 0, "terceiros": 0},
        "contagens": {},
        "buracos_reais": 0,
        "buracos_falsos": 0,
        "linhas": [],
        "linhas_falsas": [],
        "series": [],
        "mostrando": 0,
        "total_aba": 0,
        "xml_no_disco": 0,
        "pasta": "",
    }


def _consulta(empresa, competencia: str):
    return DocumentoXML.objects.filter(empresa=empresa, competencia=competencia)


def _linha(nota) -> dict:
    return {
        "modelo": nota.modelo,
        "serie": nota.serie,
        "numero": nota.numero_nota,
        "data_emissao": nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
        "status": nota.status_sefa,
        "is_propria": nota.tipo_operacao in PROPRIAS,
        "chave": nota.chave_acesso,
        "nome_arquivo": "",
        "caminho_relativo": "",
    }


def _buracos(notas) -> list[dict]:
    grupos: dict[tuple[str, str], set[int]] = defaultdict(set)
    for nota in notas:
        if nota.tipo_operacao not in PROPRIAS:
            continue
        if nota.status_sefa in {"CANCELADA", "DENEGADA", "OUTROS", "REJEITADA", "INUTILIZADA"}:
            continue
        try:
            numero = int(str(nota.numero_nota).lstrip("0") or "0")
        except ValueError:
            continue
        grupos[(nota.modelo, nota.serie)].add(numero)
    faltas = []
    for (modelo, serie), numeros in sorted(grupos.items()):
        ordenados = sorted(numeros)
        for anterior, seguinte in zip(ordenados, ordenados[1:]):
            quantidade = seguinte - anterior - 1
            if quantidade <= 0 or quantidade > SALTO_MAXIMO:
                continue
            texto = str(anterior + 1) if quantidade == 1 else f"{anterior + 1} a {seguinte - 1}"
            faltas.append({
                "tipo": modelo,
                "serie": serie,
                "numero": texto,
                "periodo": f"{quantidade} nota(s)",
                "mes_possivel": "",
                "buraco_em": "",
                "emitida_em": "",
                "status": "",
            })
    return faltas


def _series(notas) -> list[dict]:
    grupos: dict[tuple[str, str], dict] = {}
    for nota in notas:
        if nota.tipo_operacao not in PROPRIAS:
            continue
        chave = (nota.modelo, nota.serie)
        item = grupos.setdefault(chave, {
            "modelo": nota.modelo,
            "serie": nota.serie,
            "autorizadas": 0,
            "canceladas": 0,
            "inutilizadas": 0,
            "total": 0,
        })
        item["total"] += 1
        if nota.status_sefa == "AUTORIZADA":
            item["autorizadas"] += 1
        elif nota.status_sefa == "CANCELADA":
            item["canceladas"] += 1
        elif nota.status_sefa == "INUTILIZADA":
            item["inutilizadas"] += 1
    return [grupos[chave] for chave in sorted(grupos)]


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
    painel["sem_arquivo"] = False
    painel["erro"] = ""
    painel["contagens"] = contagens
    painel["resumo"] = {"total": len(notas), "propria": propria, "terceiros": len(notas) - propria}
    painel["series"] = _series(notas)
    if aba == "buracos":
        painel["linhas"] = _buracos(notas)
        painel["buracos_reais"] = len(painel["linhas"])
        painel["total_aba"] = len(painel["linhas"])
        painel["linhas"] = painel["linhas"][:LIMITE_TELA]
        painel["mostrando"] = len(painel["linhas"])
        return painel
    if aba == "series":
        painel["total_aba"] = len(painel["series"])
        painel["mostrando"] = painel["total_aba"]
        return painel
    escolhidas = notas
    status = STATUS_DA_ABA.get(aba)
    if status:
        escolhidas = [nota for nota in notas if nota.status_sefa in status]
    linhas = [_linha(nota) for nota in escolhidas]
    painel["total_aba"] = len(linhas)
    painel["linhas"] = linhas[:LIMITE_TELA]
    painel["mostrando"] = len(painel["linhas"])
    return painel


def planilha(empresa, competencia: str, aba: str) -> str:
    notas = list(_consulta(empresa, competencia))
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    if aba == "buracos":
        escritor.writerow(["Modelo", "Série", "Números faltantes", "Quantidade"])
        for linha in _buracos(notas):
            escritor.writerow([linha["tipo"], linha["serie"], linha["numero"], linha["periodo"]])
        return saida.getvalue()
    if aba == "series":
        escritor.writerow(["Modelo", "Série", "Autorizadas", "Canceladas", "Inutilizadas", "Total"])
        for linha in _series(notas):
            escritor.writerow([
                linha["modelo"], linha["serie"], linha["autorizadas"],
                linha["canceladas"], linha["inutilizadas"], linha["total"],
            ])
        return saida.getvalue()
    escritor.writerow(["Modelo", "Série", "Nota", "Emissão", "Status", "Lado", "Chave", "Valor"])
    status = STATUS_DA_ABA.get(aba)
    for nota in notas:
        if status and nota.status_sefa not in status:
            continue
        escritor.writerow([
            nota.modelo,
            nota.serie,
            nota.numero_nota,
            nota.data_emissao.strftime("%d/%m/%Y") if nota.data_emissao else "",
            nota.status_sefa,
            "Própria" if nota.tipo_operacao in PROPRIAS else "Terceiros",
            nota.chave_acesso,
            nota.valor_total,
        ])
    return saida.getvalue()


def _arquivo_da_nota(empresa, competencia: str, chave: str):
    try:
        raiz = pasta_do_mes(empresa, competencia)
    except ValueError:
        return None
    achados = [item for item in raiz.rglob(f"*{chave}*.xml") if item.is_file()]
    return achados[0] if achados else None


def zip_xml(empresa, competencia: str, aba: str) -> tuple[bytes, int, int]:
    if aba in {"buracos", "series"}:
        return b"", 0, 0
    notas = list(_consulta(empresa, competencia))
    status = STATUS_DA_ABA.get(aba)
    memoria = io.BytesIO()
    gravados = 0
    sem_arquivo = 0
    with zipfile.ZipFile(memoria, "w", compression=zipfile.ZIP_DEFLATED) as pacote:
        for nota in notas:
            if status and nota.status_sefa not in status:
                continue
            caminho = _arquivo_da_nota(empresa, competencia, nota.chave_acesso)
            if caminho is None:
                sem_arquivo += 1
                continue
            pacote.write(caminho, arcname=caminho.name)
            gravados += 1
    return memoria.getvalue(), gravados, sem_arquivo
