import unicodedata
from pathlib import Path

from django.conf import settings

from clientes.competencia import interpretar

MESES = (
    "01_JANEIRO",
    "02_FEVEREIRO",
    "03_MARCO",
    "04_ABRIL",
    "05_MAIO",
    "06_JUNHO",
    "07_JULHO",
    "08_AGOSTO",
    "09_SETEMBRO",
    "10_OUTUBRO",
    "11_NOVEMBRO",
    "12_DEZEMBRO",
)

SUBPASTAS = (
    "01_XML_BRUTOS_RECEBIDOS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/01_ENTRADAS_EMISSAO_PROPRIA/01_AUTORIZADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/01_ENTRADAS_EMISSAO_PROPRIA/02_CANCELADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/01_ENTRADAS_EMISSAO_PROPRIA/03_DENEGADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/01_ENTRADAS_EMISSAO_PROPRIA/04_REJEITADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/02_ENTRADAS_TERCEIROS/01_AUTORIZADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/02_ENTRADAS_TERCEIROS/02_CANCELADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/02_ENTRADAS_TERCEIROS/03_DENEGADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/02_ENTRADAS_TERCEIROS/04_REJEITADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/03_SAIDAS_PROPRIAS/01_AUTORIZADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/03_SAIDAS_PROPRIAS/02_CANCELADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/03_SAIDAS_PROPRIAS/03_DENEGADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/03_SAIDAS_PROPRIAS/04_REJEITADAS",
    "02_XML_PROCESSADOS_SENTINELA/01_NFE_MOD55/04_INUTILIZADAS",
    "02_XML_PROCESSADOS_SENTINELA/02_NFCE_MOD65/01_AUTORIZADAS",
    "02_XML_PROCESSADOS_SENTINELA/02_NFCE_MOD65/02_CANCELADAS",
    "02_XML_PROCESSADOS_SENTINELA/03_NFSE_PADRAO_NACIONAL/01_SERVICOS_PRESTADOS",
    "02_XML_PROCESSADOS_SENTINELA/03_NFSE_PADRAO_NACIONAL/02_SERVICOS_TOMADOS",
    "03_ARQUIVOS_DOMINIO",
    "04_RELATORIOS_AUDITORIA_E_APURACAO",
    "99_XML_NAO_PERTENCENTES_AO_CLIENTE",
)


def razao_para_pasta(razao_social: str) -> str:
    normal = unicodedata.normalize("NFKD", razao_social.upper())
    sem_acento = "".join(caractere for caractere in normal if not unicodedata.combining(caractere))
    limpa = "".join(caractere for caractere in sem_acento if caractere.isalnum() or caractere.isspace()).strip()
    return "_".join(limpa.split())


class GerenciadorPastasSharePoint:
    def __init__(self, caminho_pasta_mae: str):
        self.pasta_mae = Path(caminho_pasta_mae)

    def criar_estrutura_cliente(self, codigo_dominio: str, razao_social: str, cnpj: str, ano: str = "2026"):
        nome_pasta_cliente = f"{str(codigo_dominio).zfill(4)}_{razao_para_pasta(razao_social)}_{cnpj}"
        path_cliente = self.pasta_mae / nome_pasta_cliente
        path_ano = path_cliente / str(ano)
        for mes in MESES:
            path_mes = path_ano / mes
            for sub in SUBPASTAS:
                (path_mes / sub).mkdir(parents=True, exist_ok=True)
        return str(path_cliente)


def raiz_sharepoint() -> Path:
    return Path(settings.SHAREPOINT_ROOT_DIR)


def pasta_do_mes(empresa, competencia: str) -> Path:
    partes = interpretar(competencia)
    if partes is None:
        raise ValueError("Competência inválida.")
    mes, ano = partes
    raiz = GerenciadorPastasSharePoint(settings.PASTA_MAE).criar_estrutura_cliente(
        empresa.codigo_dominio,
        empresa.razao_social,
        empresa.cnpj,
        str(ano),
    )
    pasta = Path(raiz) / str(ano) / MESES[mes - 1]
    for sub in SUBPASTAS:
        (pasta / sub).mkdir(parents=True, exist_ok=True)
    return pasta


def pasta_relatorios(empresa, competencia: str) -> Path:
    destino = pasta_do_mes(empresa, competencia) / "04_RELATORIOS_AUDITORIA_E_APURACAO"
    destino.mkdir(parents=True, exist_ok=True)
    return destino
