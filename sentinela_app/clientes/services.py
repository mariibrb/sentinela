import unicodedata

import requests
from django.core.exceptions import ValidationError

from clientes.cnpj import digitos_conferem


def _sem_acento(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto or "")
    return "".join(caractere for caractere in normal if not unicodedata.combining(caractere)).upper().strip()


_CABECALHO = {
    "Accept": "application/json",
    "User-Agent": "Sentinela/1.0",
}


class ConsultaIndisponivel(Exception):
    """A base nacional não respondeu. O cadastro segue manual."""


class ServicoConsultaCNPJ:
    @staticmethod
    def _baixar_cadastro(cnpj):
        fontes = (
            f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
            f"https://minhareceita.org/{cnpj}",
        )
        encontrou_ausencia = False
        for url in fontes:
            try:
                response = requests.get(url, timeout=12, headers=_CABECALHO)
            except requests.RequestException:
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    continue
            if response.status_code == 404:
                encontrou_ausencia = True
                continue
        if encontrou_ausencia:
            raise ValidationError("CNPJ não encontrado nas bases nacionais. Confira os 14 dígitos.")
        raise ConsultaIndisponivel("Consulta automática indisponível no momento")

    @staticmethod
    def dados_cadastrais(cnpj_limpo):
        cnpj = "".join(caractere for caractere in str(cnpj_limpo) if caractere.isdigit())
        if len(cnpj) != 14:
            raise ValidationError("CNPJ inválido. Deve conter exatamente 14 dígitos.")
        if not digitos_conferem(cnpj):
            raise ValidationError("Os 14 números foram lidos, mas os dois últimos dígitos não conferem. Confira o CNPJ.")

        data = ServicoConsultaCNPJ._baixar_cadastro(cnpj)
        inscricoes = ServicoConsultaCNPJ.baixar_inscricoes(cnpj)
        municipio = data.get("municipio", "") or ""
        uf = data.get("uf", "") or ""
        codigo_ibge = ServicoConsultaCNPJ.buscar_codigo_ibge(municipio, uf) or str(data.get("codigo_municipio_ibge") or "")
        razao = data.get("razao_social", "") or ""
        return {
            "cnpj": cnpj,
            "razao_social": razao,
            "nome_fantasia": data.get("nome_fantasia", "") or razao,
            "tipo_estabelecimento": "Matriz" if data.get("identificador_matriz_filial") == 1 else "Filial",
            "status": "Ativo" if data.get("descricao_situacao_cadastral") == "ATIVA" else "Inativo",
            "cep": "".join(caractere for caractere in str(data.get("cep", "")) if caractere.isdigit()),
            "logradouro": data.get("logradouro", "") or "",
            "numero": data.get("numero", "") or "",
            "complemento": data.get("complemento", "") or "",
            "bairro": data.get("bairro", "") or "",
            "municipio": municipio,
            "uf": uf,
            "codigo_ibge": codigo_ibge,
            "cnae_principal": str(data.get("cnae_fiscal", "") or ""),
            "regime_tributario": "Simples Nacional" if data.get("opcao_pelo_simples") else "Lucro Presumido",
            "inscricao_estadual": ServicoConsultaCNPJ.escolher_inscricao_estadual(inscricoes, uf),
            "substituicoes": ServicoConsultaCNPJ.substituicoes_das_inscricoes(inscricoes, uf),
        }

    @staticmethod
    def escolher_inscricao_estadual(inscricoes, uf):
        sigla = (uf or "").strip().upper()
        if not sigla:
            return ""
        candidatas = []
        for item in inscricoes or []:
            if not isinstance(item, dict):
                continue
            estado = str(((item.get("estado") or {}).get("sigla") or "")).upper()
            if estado != sigla:
                continue
            numero = "".join(caractere for caractere in str(item.get("inscricao_estadual") or "") if caractere.isdigit())
            if numero:
                candidatas.append((bool(item.get("ativo")), numero))
        if not candidatas:
            return ""
        candidatas.sort(key=lambda par: not par[0])
        return candidatas[0][1]

    @staticmethod
    def substituicoes_das_inscricoes(inscricoes, uf):
        casa = (uf or "").strip().upper()
        resultado = {}
        for item in inscricoes or []:
            if not isinstance(item, dict) or not item.get("ativo"):
                continue
            estado = str(((item.get("estado") or {}).get("sigla") or "")).upper()
            numero = "".join(caractere for caractere in str(item.get("inscricao_estadual") or "") if caractere.isdigit())
            if estado and estado != casa and numero and estado not in resultado:
                resultado[estado] = numero
        return resultado

    @staticmethod
    def baixar_inscricoes(cnpj):
        try:
            response = requests.get(
                f"https://publica.cnpj.ws/cnpj/{cnpj}",
                timeout=12,
                headers=_CABECALHO,
            )
            if response.status_code != 200:
                return []
            estabelecimento = response.json().get("estabelecimento") or {}
            return estabelecimento.get("inscricoes_estaduais") or []
        except (requests.RequestException, ValueError, TypeError):
            return []

    @staticmethod
    def buscar_codigo_ibge(municipio, uf):
        """Consulta a API oficial do IBGE para obter o código de 7 dígitos do município."""
        if not municipio or not uf:
            return ""
        try:
            resp = requests.get(
                f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios",
                timeout=5,
            )
            if resp.status_code == 200:
                alvo = _sem_acento(municipio)
                for item in resp.json():
                    if _sem_acento(item.get("nome", "")) == alvo:
                        return str(item["id"])
        except (requests.RequestException, ValueError, TypeError):
            return ""
        return ""
