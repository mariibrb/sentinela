from django.conf import settings
from django.utils import timezone

from clientes.utils_sharepoint import GerenciadorPastasSharePoint, raiz_sharepoint


def criar_pastas(empresa) -> str:
    ano = str(timezone.localdate().year)
    raiz_sharepoint().mkdir(parents=True, exist_ok=True)
    return GerenciadorPastasSharePoint(settings.PASTA_MAE).criar_estrutura_cliente(
        empresa.codigo_dominio,
        empresa.razao_social,
        empresa.cnpj,
        ano,
    )
