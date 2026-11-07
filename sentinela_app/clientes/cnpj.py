import json
from urllib.error import URLError
from urllib.request import Request, urlopen


class ConsultaCnpjErro(Exception):
    pass


def somente_digitos(texto: str) -> str:
    return "".join(caractere for caractere in str(texto or "") if caractere.isdigit())


def digitos_conferem(cnpj: str) -> bool:
    numero = somente_digitos(cnpj)
    if len(numero) != 14 or numero == numero[0] * 14:
        return False

    def calcular(base, pesos):
        resto = sum(int(digito) * peso for digito, peso in zip(base, pesos)) % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = calcular(numero[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    segundo = calcular(numero[:12] + primeiro, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return numero[-2:] == primeiro + segundo


def consultar_cnpj(cnpj: str) -> dict:
    numero = somente_digitos(cnpj)
    if len(numero) != 14:
        raise ConsultaCnpjErro("Informe um CNPJ com 14 dígitos.")
    pedido = Request(
        f"https://brasilapi.com.br/api/cnpj/v1/{numero}",
        headers={"Accept": "application/json", "User-Agent": "Sentinela"},
    )
    try:
        with urlopen(pedido, timeout=20) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except URLError as erro:
        raise ConsultaCnpjErro("A consulta do CNPJ não respondeu.") from erro
    except json.JSONDecodeError as erro:
        raise ConsultaCnpjErro("A consulta do CNPJ devolveu uma resposta incompleta.") from erro
    tipo = "Matriz" if dados.get("identificador_matriz_filial") == 1 else "Filial"
    return {
        "cnpj": numero,
        "razao_social": dados.get("razao_social") or "",
        "nome_fantasia": dados.get("nome_fantasia") or "",
        "tipo_estabelecimento": tipo,
        "cnae_principal": str(dados.get("cnae_fiscal") or ""),
        "cep": somente_digitos(dados.get("cep") or ""),
        "logradouro": dados.get("logradouro") or "",
        "numero": dados.get("numero") or "",
        "complemento": dados.get("complemento") or "",
        "bairro": dados.get("bairro") or "",
        "uf": dados.get("uf") or "",
        "municipio": dados.get("municipio") or "",
        "codigo_ibge": str(dados.get("codigo_municipio_ibge") or ""),
    }
