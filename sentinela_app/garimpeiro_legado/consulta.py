"""Painel do que o Garimpeiro já leu: listas, buracos e extração."""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

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
    "autorizadas": "NORMAIS",
    "canceladas": "CANCELADOS",
    "inutilizadas": "INUTILIZADOS",
    "denegadas": "DENEGADOS",
    "rejeitadas": "REJEITADOS",
}
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


def _motor():
    from garimpeiro_legado.ponte import carregar, pasta_resultados

    return carregar(), pasta_resultados()


def _conn(motor, cnpj):
    if not motor._arquivo_fiscal_db_path(cnpj).is_file():
        return None
    return motor._arquivo_fiscal_conn(cnpj)


def _contagens(conn, cnpj: str, ano: int, mes: int) -> dict:
    linhas = conn.execute(
        """
        SELECT upper(trim(coalesce(status, ''))), is_propria, COUNT(*)
        FROM xml_arquivo
        WHERE cnpj_empresa = ? AND ano = ? AND mes = ?
        GROUP BY 1, 2
        """,
        (cnpj, ano, mes),
    ).fetchall()
    contagens = {"NORMAIS": 0, "CANCELADOS": 0, "INUTILIZADOS": 0, "DENEGADOS": 0, "REJEITADOS": 0}
    propria = terceiros = 0
    for status, lado, quantidade in linhas:
        contagens[status] = contagens.get(status, 0) + int(quantidade)
        if int(lado or 0) == 1:
            propria += int(quantidade)
        else:
            terceiros += int(quantidade)
    contagens["total"] = propria + terceiros
    contagens["propria"] = propria
    contagens["terceiros"] = terceiros
    return contagens


def _linhas_status(conn, cnpj: str, ano: int, mes: int, status: str | None, limite: int | None):
    sql = """
        SELECT modelo, serie, numero, data_emissao, status, is_propria, chave, nome_arquivo, caminho_relativo
        FROM xml_arquivo
        WHERE cnpj_empresa = ? AND ano = ? AND mes = ?
    """
    params: list = [cnpj, ano, mes]
    if status:
        sql += " AND upper(trim(coalesce(status, ''))) = ?"
        params.append(status)
    sql += " ORDER BY is_propria DESC, modelo, serie, numero"
    if limite:
        sql += " LIMIT ?"
        params.append(int(limite))
    colunas = (
        "modelo",
        "serie",
        "numero",
        "data_emissao",
        "status",
        "is_propria",
        "chave",
        "nome_arquivo",
        "caminho_relativo",
    )
    return [dict(zip(colunas, linha)) for linha in conn.execute(sql, params).fetchall()]


def _series(conn, cnpj: str, ano: int, mes: int) -> list[dict]:
    linhas = conn.execute(
        """
        SELECT modelo, serie,
               SUM(CASE WHEN upper(trim(coalesce(status, ''))) = 'NORMAIS' THEN 1 ELSE 0 END),
               SUM(CASE WHEN upper(trim(coalesce(status, ''))) = 'CANCELADOS' THEN 1 ELSE 0 END),
               SUM(CASE WHEN upper(trim(coalesce(status, ''))) = 'INUTILIZADOS' THEN 1 ELSE 0 END),
               COUNT(*)
        FROM xml_arquivo
        WHERE cnpj_empresa = ? AND ano = ? AND mes = ? AND is_propria = 1
        GROUP BY modelo, serie
        ORDER BY modelo, serie
        """,
        (cnpj, ano, mes),
    ).fetchall()
    return [
        {
            "modelo": modelo,
            "serie": serie,
            "autorizadas": int(autorizadas or 0),
            "canceladas": int(canceladas or 0),
            "inutilizadas": int(inutilizadas or 0),
            "total": int(total or 0),
        }
        for modelo, serie, autorizadas, canceladas, inutilizadas, total in linhas
    ]


def _buracos(motor, cnpj: str, ano: int, mes: int) -> tuple[list[dict], list[dict]]:
    reais, falsos = motor._arquivo_fiscal_buracos_painel_periodo(cnpj, ano, [mes])
    return _quadro(reais), _quadro(falsos)


def _quadro(tabela) -> list[dict]:
    if tabela is None or getattr(tabela, "empty", True):
        return []
    saida = []
    for linha in tabela.fillna("").to_dict("records"):
        saida.append({
            "tipo": linha.get("Tipo", ""),
            "serie": linha.get("Série") or linha.get("Serie", ""),
            "numero": linha.get("Num_Faltante", ""),
            "periodo": linha.get("Período") or linha.get("Periodo", ""),
            "mes_possivel": linha.get("Mês possível", ""),
            "buraco_em": linha.get("Buraco em", ""),
            "emitida_em": linha.get("Emitida em", ""),
            "status": linha.get("Status NF", ""),
        })
    return saida


def montar(cnpj: str, ano: int, mes: int, aba: str) -> dict:
    try:
        motor, pasta = _motor()
    except Exception as erro:
        return _vazio(f"O Garimpeiro não abriu. {erro}")
    cnpj = motor._arquivo_fiscal_cnpj_limpo(cnpj)
    painel = _vazio()
    painel["pasta"] = str(pasta)
    painel["erro"] = ""
    if len(cnpj) != 14:
        painel["erro"] = "CNPJ da empresa incompleto."
        return painel
    conn = _conn(motor, cnpj)
    if conn is None:
        painel["erro"] = "Ainda não há leitura desta empresa no arquivo do Garimpeiro."
        return painel
    try:
        contagens = _contagens(conn, cnpj, ano, mes)
        painel["sem_arquivo"] = False
        painel["contagens"] = contagens
        painel["resumo"] = {
            "total": contagens.get("total", 0),
            "propria": contagens.get("propria", 0),
            "terceiros": contagens.get("terceiros", 0),
        }
        painel["series"] = _series(conn, cnpj, ano, mes)
        status = STATUS_DA_ABA.get(aba)
        if aba == "buracos":
            painel["linhas"], painel["linhas_falsas"] = _buracos(motor, cnpj, ano, mes)
            painel["buracos_reais"] = len(painel["linhas"])
            painel["buracos_falsos"] = len(painel["linhas_falsas"])
            painel["total_aba"] = painel["buracos_reais"]
            painel["linhas"] = painel["linhas"][:LIMITE_TELA]
            painel["mostrando"] = len(painel["linhas"])
        elif aba == "series":
            painel["total_aba"] = len(painel["series"])
            painel["mostrando"] = painel["total_aba"]
        else:
            total = contagens.get("total", 0) if status is None else contagens.get(status, 0)
            painel["total_aba"] = int(total)
            painel["linhas"] = _linhas_status(conn, cnpj, ano, mes, status, LIMITE_TELA)
            painel["mostrando"] = len(painel["linhas"])
            painel["xml_no_disco"] = sum(1 for linha in painel["linhas"] if _arquivo_da_linha(motor, cnpj, linha))
    finally:
        conn.close()
    return painel


def _arquivo_da_linha(motor, cnpj: str, linha: dict) -> Path | None:
    relativo = str(linha.get("caminho_relativo") or "").strip()
    if not relativo:
        return None
    try:
        base = motor._arquivo_fiscal_pasta_empresa(cnpj)
    except Exception:
        return None
    candidato = (base / relativo).resolve()
    if candidato.is_file():
        return candidato
    return None


def planilha(cnpj: str, ano: int, mes: int, aba: str) -> str:
    motor, _pasta = _motor()
    cnpj = motor._arquivo_fiscal_cnpj_limpo(cnpj)
    conn = _conn(motor, cnpj)
    saida = io.StringIO()
    escritor = csv.writer(saida, delimiter=";")
    if conn is None:
        escritor.writerow(["Sem leitura no arquivo"])
        return saida.getvalue()
    try:
        if aba == "buracos":
            reais, falsos = _buracos(motor, cnpj, ano, mes)
            escritor.writerow(["Tipo", "Série", "Número faltante", "Período", "Mês possível", "Situação"])
            for linha in reais:
                escritor.writerow([
                    linha["tipo"], linha["serie"], linha["numero"],
                    linha["periodo"], linha["mes_possivel"], "Buraco",
                ])
            for linha in falsos:
                escritor.writerow([
                    linha["tipo"], linha["serie"], linha["numero"],
                    linha["buraco_em"], linha["emitida_em"], "Nota em outro mês",
                ])
        elif aba == "series":
            escritor.writerow(["Modelo", "Série", "Autorizadas", "Canceladas", "Inutilizadas", "Total"])
            for linha in _series(conn, cnpj, ano, mes):
                escritor.writerow([
                    linha["modelo"], linha["serie"], linha["autorizadas"],
                    linha["canceladas"], linha["inutilizadas"], linha["total"],
                ])
        else:
            escritor.writerow(["Modelo", "Série", "Nota", "Emissão", "Status", "Lado", "Chave"])
            status = STATUS_DA_ABA.get(aba)
            for linha in _linhas_status(conn, cnpj, ano, mes, status, None):
                escritor.writerow([
                    linha["modelo"],
                    linha["serie"],
                    linha["numero"],
                    linha["data_emissao"],
                    linha["status"],
                    "Própria" if int(linha["is_propria"] or 0) == 1 else "Terceiros",
                    linha["chave"],
                ])
    finally:
        conn.close()
    return saida.getvalue()


def zip_xml(cnpj: str, ano: int, mes: int, aba: str) -> tuple[bytes, int, int]:
    """Devolve o zip, quantos XML entraram e quantas linhas não tinham arquivo."""
    motor, _pasta = _motor()
    cnpj = motor._arquivo_fiscal_cnpj_limpo(cnpj)
    conn = _conn(motor, cnpj)
    if conn is None or aba in {"buracos", "series"}:
        return b"", 0, 0
    status = STATUS_DA_ABA.get(aba)
    try:
        linhas = _linhas_status(conn, cnpj, ano, mes, status, None)
    finally:
        conn.close()
    memoria = io.BytesIO()
    gravados = 0
    sem_arquivo = 0
    with zipfile.ZipFile(memoria, "w", compression=zipfile.ZIP_DEFLATED) as pacote:
        for linha in linhas:
            caminho = _arquivo_da_linha(motor, cnpj, linha)
            if caminho is None:
                sem_arquivo += 1
                continue
            pacote.write(caminho, arcname=caminho.name)
            gravados += 1
    return memoria.getvalue(), gravados, sem_arquivo
