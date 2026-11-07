"""Motor de leitura e triagem de XML do Sentinela.

O aplicativo Django usa a versão em sentinela_app/triagem_xml/motor.py.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

_CANCELADA = {"101", "151", "155"}
_AUTORIZADA = {"100", "150"}
_DENEGADA = {"110", "301", "302", "303"}
_EVENTO_CANCELADO = {"135", "136", "155"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _primeiro(elemento, nome: str):
    for item in elemento.iter():
        if _local(item.tag) == nome:
            return item
    return None


def _texto(elemento, nome: str) -> str:
    achado = _primeiro(elemento, nome)
    if achado is None or not achado.text:
        return ""
    return achado.text.strip()


class SentinelaMotorTriagem:
    def __init__(self, cnpj_cliente_alvo: str):
        self.cnpj_cliente = self.limpar_cnpj(cnpj_cliente_alvo)

    def limpar_cnpj(self, texto) -> str:
        if not texto:
            return ""
        return "".join(caractere for caractere in str(texto) if caractere.isdigit())

    def identificar_tipo_e_status_nfe(self, root):
        """Devolve modelo, direção, status, chave e número.

        Direção: SAIDA_PROPRIA, ENTRADA_EMISSAO_PROPRIA, ENTRADA_TERCEIROS,
        NAO_PERTENCE_AO_CLIENTE ou INVALIDO.
        Status: AUTORIZADA, CANCELADA, DENEGADA, REJEITADA_OU_OUTROS ou INVALIDO.
        """
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
            if _texto(proc, "tpEvento") == "110111" and _texto(_primeiro(proc, "retEvento") or proc, "cStat") in _EVENTO_CANCELADO:
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

    def processar_arquivo_xml(self, caminho_xml, pasta_destino_base) -> bool:
        """Copia o XML para a pasta da triagem. O arquivo de origem permanece."""
        caminho = Path(caminho_xml)
        try:
            root = ET.parse(caminho).getroot()
        except ET.ParseError:
            return False

        if "inutNFe" in root.tag or _primeiro(root, "inutNFe") is not None:
            subpasta = Path(pasta_destino_base) / "04_INUTILIZADAS"
            subpasta.mkdir(parents=True, exist_ok=True)
            shutil.copy(caminho, subpasta / caminho.name)
            return True

        mod, direcao, status, chave, n_nf = self.identificar_tipo_e_status_nfe(root)
        if direcao == "INVALIDO":
            return False

        if direcao == "NAO_PERTENCE_AO_CLIENTE":
            subpasta = Path(pasta_destino_base) / "99_XML_NAO_PERTENCENTES_AO_CLIENTE"
            subpasta.mkdir(parents=True, exist_ok=True)
            shutil.copy(caminho, subpasta / caminho.name)
            return True

        numero_formatado = f"NF{str(n_nf).zfill(9)}"
        nome_padrao = f"{numero_formatado}_{chave}.xml" if chave else caminho.name

        if mod == "65":
            pasta_mod = Path(pasta_destino_base) / "02_NFCE_MOD65"
            if status == "AUTORIZADA":
                subpasta = pasta_mod / "01_AUTORIZADAS"
            elif status == "CANCELADA":
                subpasta = pasta_mod / "02_CANCELADAS"
            else:
                subpasta = pasta_mod / "04_REJEITADAS_OU_CONTINGENCIA"
        else:
            pasta_mod = Path(pasta_destino_base) / "01_NFE_MOD55"
            if direcao == "ENTRADA_EMISSAO_PROPRIA":
                dir_path = "01_ENTRADAS_EMISSAO_PROPRIA"
            elif direcao == "ENTRADA_TERCEIROS":
                dir_path = "02_ENTRADAS_TERCEIROS"
            else:
                dir_path = "03_SAIDAS_PROPRIAS"
            if status == "AUTORIZADA":
                subpasta = pasta_mod / dir_path / "01_AUTORIZADAS"
            elif status == "CANCELADA":
                subpasta = pasta_mod / dir_path / "02_CANCELADAS"
            elif status == "DENEGADA":
                subpasta = pasta_mod / dir_path / "03_DENEGADAS"
            else:
                subpasta = pasta_mod / dir_path / "04_REJEITADAS"

        subpasta.mkdir(parents=True, exist_ok=True)
        shutil.copy(caminho, subpasta / nome_padrao)
        return True

    def processar_pasta(self, pasta_origem, pasta_destino_base) -> dict:
        """Lê cada XML da pasta, inclusive os que estão dentro de subpastas."""
        origem = Path(pasta_origem)
        resumo = {"copiados": 0, "ignorados": 0}
        for caminho in sorted(origem.rglob("*.xml")):
            if self.processar_arquivo_xml(caminho, pasta_destino_base):
                resumo["copiados"] += 1
            else:
                resumo["ignorados"] += 1
        return resumo
