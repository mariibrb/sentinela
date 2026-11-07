"""Leitura e triagem de NF-e e NFC-e do Sentinela."""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

_CANCELADA = {"101", "151", "155"}
_AUTORIZADA = {"100", "150"}
_DENEGADA = {"110", "301", "302", "303"}
_EVENTO_CANCELADO = {"135", "136", "155"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _primeiro(elemento, nome: str):
    if elemento is None:
        return None
    for item in elemento.iter():
        if _local(item.tag) == nome:
            return item
    return None


def _texto(elemento, nome: str) -> str:
    achado = _primeiro(elemento, nome)
    if achado is None or not achado.text:
        return ""
    return achado.text.strip()


def _decimal(texto: str) -> Decimal:
    bruto = (texto or "").strip()
    if not bruto:
        return Decimal("0.00")
    if "," in bruto and "." in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    try:
        return Decimal(bruto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _data(texto: str, chave: str) -> date | None:
    bruto = (texto or "").strip()
    if len(bruto) >= 10 and bruto[4] == "-" and bruto[7] == "-":
        ano, mes, dia = bruto[:4], bruto[5:7], bruto[8:10]
        if ano.isdigit() and mes.isdigit() and dia.isdigit():
            return date(int(ano), int(mes), int(dia))
    if len(bruto) >= 8 and bruto[:8].isdigit():
        return date(int(bruto[:4]), int(bruto[4:6]), int(bruto[6:8]))
    if len(chave) == 44 and chave.isdigit():
        mes = int(chave[4:6])
        if 1 <= mes <= 12:
            return date(2000 + int(chave[2:4]), mes, 1)
    return None


class SentinelaMotorTriagem:
    def __init__(self, cnpj_cliente_alvo: str):
        self.cnpj_cliente = self.limpar_cnpj(cnpj_cliente_alvo)

    def limpar_cnpj(self, texto) -> str:
        if not texto:
            return ""
        return "".join(caractere for caractere in str(texto) if caractere.isdigit())

    def identificar_tipo_e_status_nfe(self, root):
        inf_nfe = _primeiro(root, "infNFe")
        if inf_nfe is None:
            return "", "INVALIDO", "INVALIDO", "", ""

        identificador = inf_nfe.attrib.get("Id", "")
        chave = identificador.replace("NFe", "") if identificador else ""
        ide = _primeiro(inf_nfe, "ide")
        mod = _texto(ide, "mod") if ide is not None else ""
        n_nf = _texto(ide, "nNF") if ide is not None else ""
        tp_nf = _texto(ide, "tpNF") if ide is not None else ""
        if not mod:
            mod = "55"
        if not n_nf:
            n_nf = "0"
        if not tp_nf:
            tp_nf = "1"

        emit = _primeiro(inf_nfe, "emit")
        dest = _primeiro(inf_nfe, "dest")
        cnpj_emit = self.limpar_cnpj(_texto(emit, "CNPJ")) if emit is not None else ""
        cnpj_dest = self.limpar_cnpj(_texto(dest, "CNPJ")) if dest is not None else ""
        participa = self.cnpj_cliente in {cnpj_emit, cnpj_dest} and len(self.cnpj_cliente) == 14
        if not participa:
            return mod, "NAO_PERTENCE_AO_CLIENTE", "", chave, n_nf

        prot = _primeiro(root, "protNFe")
        c_stat = _texto(prot, "cStat") if prot is not None else ""
        evento_cancelado = False
        for proc in root.iter():
            if _local(proc.tag) != "procEventoNFe":
                continue
            retorno = _primeiro(proc, "retEvento") or proc
            if _texto(proc, "tpEvento") == "110111" and _texto(retorno, "cStat") in _EVENTO_CANCELADO:
                evento_cancelado = True
                break
        if not evento_cancelado:
            ret_evento = _primeiro(root, "retEvento")
            if ret_evento is not None and _texto(root, "tpEvento") == "110111" and _texto(ret_evento, "cStat") in _EVENTO_CANCELADO:
                evento_cancelado = True

        if evento_cancelado or c_stat in _CANCELADA:
            status_nota = "CANCELADA"
        elif c_stat in _AUTORIZADA:
            status_nota = "AUTORIZADA"
        elif c_stat in _DENEGADA:
            status_nota = "DENEGADA"
        else:
            status_nota = "REJEITADA_OU_OUTROS"

        if cnpj_emit == self.cnpj_cliente:
            direcao = "SAIDA_PROPRIA" if tp_nf == "1" else "ENTRADA_EMISSAO_PROPRIA"
        else:
            direcao = "ENTRADA_TERCEIROS"
        return mod, direcao, status_nota, chave, n_nf

    def ler_fiscais(self, root, chave: str) -> dict:
        inf_nfe = _primeiro(root, "infNFe")
        ide = _primeiro(inf_nfe, "ide") if inf_nfe is not None else None
        totais = _primeiro(inf_nfe, "ICMSTot") if inf_nfe is not None else None
        emissao = _data(_texto(ide, "dhEmi") or _texto(ide, "dEmi"), chave)
        return {
            "serie": (_texto(ide, "serie") if ide is not None else "") or (chave[22:25].lstrip("0") or "0" if len(chave) == 44 else ""),
            "data_emissao": emissao,
            "competencia": f"{emissao.month:02d}{emissao.year}" if emissao else "",
            "valor_total": _decimal(_texto(totais, "vNF")),
            "base_icms": _decimal(_texto(totais, "vBC")),
            "valor_icms": _decimal(_texto(totais, "vICMS")),
            "base_icms_st": _decimal(_texto(totais, "vBCST")),
            "valor_icms_st": _decimal(_texto(totais, "vST")),
            "valor_ipi": _decimal(_texto(totais, "vIPI")),
            "valor_pis": _decimal(_texto(totais, "vPIS")),
            "valor_cofins": _decimal(_texto(totais, "vCOFINS")),
        }

    def processar_arquivo_xml(self, caminho_xml, pasta_destino_base) -> dict:
        caminho = Path(caminho_xml)
        try:
            root = ET.parse(caminho).getroot()
        except ET.ParseError:
            return {"ok": False, "motivo": "XML ilegível"}

        if "inutNFe" in root.tag or _primeiro(root, "inutNFe") is not None:
            subpasta = Path(pasta_destino_base) / "04_INUTILIZADAS"
            subpasta.mkdir(parents=True, exist_ok=True)
            destino = subpasta / caminho.name
            shutil.copy(caminho, destino)
            return {"ok": True, "inutilizada": True, "destino": str(destino), "direcao": "", "status": ""}

        mod, direcao, status, chave, n_nf = self.identificar_tipo_e_status_nfe(root)
        if direcao == "INVALIDO":
            return {"ok": False, "motivo": "XML sem NF-e"}

        if direcao == "NAO_PERTENCE_AO_CLIENTE":
            subpasta = Path(pasta_destino_base) / "99_XML_NAO_PERTENCENTES_AO_CLIENTE"
            subpasta.mkdir(parents=True, exist_ok=True)
            destino = subpasta / caminho.name
            shutil.copy(caminho, destino)
            return {"ok": True, "inutilizada": False, "direcao": direcao, "status": "", "destino": str(destino), "chave": chave}

        fiscais = self.ler_fiscais(root, chave)
        numero_formatado = f"NF{str(n_nf).zfill(9)}"
        nome_padrao = f"{numero_formatado}_{chave}.xml" if chave else caminho.name
        if mod == "65":
            pasta_mod = Path(pasta_destino_base) / "02_NFCE_MOD65"
            pasta_status = {"AUTORIZADA": "01_AUTORIZADAS", "CANCELADA": "02_CANCELADAS"}.get(status, "04_REJEITADAS_OU_CONTINGENCIA")
            subpasta = pasta_mod / pasta_status
        else:
            pasta_mod = Path(pasta_destino_base) / "01_NFE_MOD55"
            direcoes = {
                "ENTRADA_EMISSAO_PROPRIA": "01_ENTRADAS_EMISSAO_PROPRIA",
                "ENTRADA_TERCEIROS": "02_ENTRADAS_TERCEIROS",
            }
            pasta_status = {
                "AUTORIZADA": "01_AUTORIZADAS",
                "CANCELADA": "02_CANCELADAS",
                "DENEGADA": "03_DENEGADAS",
            }.get(status, "04_REJEITADAS")
            subpasta = pasta_mod / direcoes.get(direcao, "03_SAIDAS_PROPRIAS") / pasta_status
        subpasta.mkdir(parents=True, exist_ok=True)
        destino = subpasta / nome_padrao
        shutil.copy(caminho, destino)
        return {
            "ok": True,
            "inutilizada": False,
            "destino": str(destino),
            "modelo": mod,
            "direcao": direcao,
            "status": status,
            "chave": chave,
            "numero": n_nf,
            "serie": fiscais["serie"][:10],
            "data_emissao": fiscais["data_emissao"],
            "competencia": fiscais["competencia"],
            "valores": fiscais,
        }
