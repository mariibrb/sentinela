import os
import shutil
import zipfile
from datetime import date
from pathlib import Path

from clientes.models import DocumentoXML
from triagem_xml.motor import SentinelaMotorTriagem

# Doze zips encaixados. Pasta no meio não conta como nível.
_PROFUNDIDADE_MAXIMA_ZIP = 11
_PASTA_STATUS = {
    "AUTORIZADA": "01_AUTORIZADAS",
    "CANCELADA": "02_CANCELADAS",
    "DENEGADA": "03_DENEGADAS",
    "OUTROS": "04_REJEITADAS",
}
_PASTA_DIRECAO = {
    "ENTRADA_EMISSAO_PROPRIA": "01_ENTRADAS_EMISSAO_PROPRIA",
    "ENTRADA_TERCEIROS": "02_ENTRADAS_TERCEIROS",
    "SAIDA_PROPRIA": "03_SAIDAS_PROPRIAS",
}


class MotorGarimpeiroSentinela:
    def __init__(self, empresa_obj, competencia_mmaaaa: str, pasta_cliente_sharepoint: str):
        self.empresa = empresa_obj
        self.cnpj_cliente = "".join(caractere for caractere in str(empresa_obj.cnpj) if caractere.isdigit())
        self.competencia = competencia_mmaaaa
        self.pasta_base_mes = Path(pasta_cliente_sharepoint)
        self._leitor = SentinelaMotorTriagem(self.cnpj_cliente)
        self._zips_lidos = set()
        self._seq_zip = 0

    def extrair_zip_recursivo(self, caminho_zip, pasta_destino, profundidade=0, raiz_curta=None):
        """Descompacta o ZIP e os ZIP que estiverem dentro, sem sair da pasta de destino."""
        if profundidade > _PROFUNDIDADE_MAXIMA_ZIP:
            return
        origem = Path(caminho_zip).resolve()
        if str(origem) in self._zips_lidos:
            return
        self._zips_lidos.add(str(origem))
        destino = Path(pasta_destino).resolve()
        raiz = Path(raiz_curta or destino).resolve()
        destino.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(origem) as pacote:
                for info in pacote.infolist():
                    nome = Path(info.filename)
                    if nome.is_absolute() or ".." in nome.parts:
                        continue
                    alvo = (destino / nome).resolve()
                    try:
                        alvo.relative_to(destino)
                    except ValueError:
                        continue
                    if info.is_dir():
                        alvo.mkdir(parents=True, exist_ok=True)
                        continue
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    alvo.write_bytes(pacote.read(info))
        except (zipfile.BadZipFile, OSError):
            return
        for encontrado in list(destino.rglob("*.zip")):
            self._seq_zip += 1
            self.extrair_zip_recursivo(encontrado, raiz / f"z{self._seq_zip}", profundidade + 1, raiz)

    def processar_lote_arquivos(self, caminho_origem_lote):
        pasta_temp = self.pasta_base_mes / "01_XML_BRUTOS_RECEBIDOS" / "temp_processamento"
        if pasta_temp.exists():
            shutil.rmtree(pasta_temp, ignore_errors=True)
        pasta_temp.mkdir(parents=True, exist_ok=True)
        origem = str(caminho_origem_lote)
        if os.path.isfile(origem) and origem.lower().endswith(".zip"):
            self.extrair_zip_recursivo(origem, pasta_temp)
        elif os.path.isdir(origem):
            sequencia = 0
            for raiz, _dirs, arquivos in os.walk(origem):
                for nome in arquivos:
                    caminho = os.path.join(raiz, nome)
                    if nome.lower().endswith(".zip"):
                        self.extrair_zip_recursivo(caminho, pasta_temp)
                    elif nome.lower().endswith(".xml"):
                        destino_xml = pasta_temp / nome
                        if destino_xml.exists():
                            sequencia += 1
                            destino_xml = pasta_temp / f"{sequencia}_{nome}"
                        shutil.copy(caminho, destino_xml)
        contador = 0
        for raiz, _dirs, arquivos in os.walk(pasta_temp):
            for nome in arquivos:
                if nome.lower().endswith(".xml") and self._processar_xml_individual(os.path.join(raiz, nome)):
                    contador += 1
        shutil.rmtree(pasta_temp, ignore_errors=True)
        return contador

    def _processar_xml_individual(self, caminho_xml):
        import xml.etree.ElementTree as ET

        caminho = Path(caminho_xml)
        try:
            root = ET.parse(caminho).getroot()
        except ET.ParseError:
            return False
        if "inutnfe" in root.tag.lower() or _tem_inutilizacao(root):
            subpasta = self.pasta_base_mes / "02_XML_PROCESSADOS_SENTINELA" / "01_NFE_MOD55" / "04_INUTILIZADAS"
            subpasta.mkdir(parents=True, exist_ok=True)
            shutil.copy(caminho, subpasta / caminho.name)
            return True

        modelo, direcao, status, chave, numero = self._leitor.identificar_tipo_e_status_nfe(root)
        if direcao == "INVALIDO":
            from triagem_xml.coletor_nfse import importar_xml_nfse

            return importar_xml_nfse(self.empresa, self.competencia, caminho) is not None
        if direcao == "NAO_PERTENCE_AO_CLIENTE":
            subpasta = self.pasta_base_mes / "99_XML_NAO_PERTENCENTES_AO_CLIENTE"
            subpasta.mkdir(parents=True, exist_ok=True)
            shutil.copy(caminho, subpasta / caminho.name)
            return True
        if status == "REJEITADA_OU_OUTROS":
            status = "OUTROS"
        fiscais = self._leitor.ler_fiscais(root, chave)
        self._gravar_documento(chave, numero, modelo, direcao, status, fiscais)
        nome = f"NF{str(numero).zfill(9)}_{chave}.xml" if len(chave) == 44 else caminho.name
        base = self.pasta_base_mes / "02_XML_PROCESSADOS_SENTINELA"
        if modelo == "65":
            pasta_status = "01_AUTORIZADAS" if status == "AUTORIZADA" else "02_CANCELADAS"
            subpasta = base / "02_NFCE_MOD65" / pasta_status
        else:
            subpasta = base / "01_NFE_MOD55" / _PASTA_DIRECAO.get(direcao, "03_SAIDAS_PROPRIAS") / _PASTA_STATUS.get(status, "04_REJEITADAS")
        subpasta.mkdir(parents=True, exist_ok=True)
        shutil.copy(caminho, subpasta / nome)
        return True

    def _gravar_documento(self, chave, numero, modelo, direcao, status, fiscais):
        if len(chave) != 44:
            return
        existente = DocumentoXML.objects.filter(chave_acesso=chave).first()
        if existente is not None and existente.empresa_id != self.empresa.id:
            return
        DocumentoXML.objects.update_or_create(
            chave_acesso=chave,
            defaults={
                "empresa": self.empresa,
                "competencia": self.competencia,
                "modelo": (modelo or "55")[:2],
                "numero_nota": str(numero)[:20],
                "serie": (fiscais.get("serie") or "1")[:10],
                "data_emissao": fiscais.get("data_emissao") or date.today(),
                "tipo_operacao": direcao,
                "status_sefa": status,
                "valor_total": fiscais["valor_total"],
                "base_icms": fiscais["base_icms"],
                "valor_icms": fiscais["valor_icms"],
                "base_icms_st": fiscais["base_icms_st"],
                "valor_icms_st": fiscais["valor_icms_st"],
                "valor_ipi": fiscais["valor_ipi"],
                "valor_pis": fiscais["valor_pis"],
                "valor_cofins": fiscais["valor_cofins"],
                "possui_divergencia": False,
                "mensagem_divergencia": "",
                "sugestao_correcao": "",
            },
        )


def _tem_inutilizacao(root) -> bool:
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] == "inutNFe":
            return True
    return False
