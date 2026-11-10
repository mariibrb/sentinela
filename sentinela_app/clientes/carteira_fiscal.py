"""Importa a carteira Fiscal (CÓD + CNPJ + quem executa + revisador).

Lê a aba EMPRESAS da planilha do departamento Fiscal e grava/atualiza só:
código Domínio, CNPJ, responsável operacional fiscal e revisador fiscal.
Novos clientes disparam o Buscar CNPJ para completar o cadastro mínimo.
"""
from __future__ import annotations

import io
import re
import time
import unicodedata
from typing import Any

from openpyxl import load_workbook

from clientes.cnpj import digitos_conferem, somente_digitos
from clientes.escopo import (
    ITENS_CONTABEIS,
    ITENS_DP,
    ITENS_FISCAIS,
    ITENS_REGULARIZACAO,
)
from clientes.models import Colaborador, Empresa, InscricaoSubstituto
from clientes.pastas import criar_pastas
from clientes.services import ServicoConsultaCNPJ
from clientes.trilha import foto_empresa, registrar, registrar_empresa

EQUIPE_FISCAL_PADRAO = (
    ("Giovanna", "Coordenação Fiscal"),
    ("Mariana Barbosa", "Analista Fiscal Pleno"),
    ("Clayton Bueno", "Analista Fiscal Júnior"),
    ("Ingrid", "Assistente Fiscal"),
    ("Joana", "Auxiliar Fiscal"),
    ("Paula", "Auxiliar Fiscal"),
    ("Taina Alves", "Operacional RET"),
)

PAUSA_ENTRE_CONSULTAS_S = 0.35


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch)).casefold().strip()


def garantir_equipe_fiscal() -> list[Colaborador]:
    """Garante os colaboradores do Fiscal usados na planilha de carteira."""
    criados: list[Colaborador] = []
    for nome, cargo in EQUIPE_FISCAL_PADRAO:
        chave = _sem_acento(nome)
        primeiro = chave.split()[0]
        existente = next(
            (col for col in Colaborador.objects.all() if _sem_acento(col.nome) == chave),
            None,
        )
        if existente is None:
            existente = next(
                (
                    col
                    for col in Colaborador.objects.all()
                    if _sem_acento(col.nome).split()[:1] == [primeiro]
                ),
                None,
            )
        if existente is None:
            existente = Colaborador.objects.create(nome=nome, cargo=cargo, ativo=True)
            criados.append(existente)
        elif not existente.ativo:
            existente.ativo = True
            existente.save(update_fields=["ativo"])
    return criados


def resolver_colaborador_fiscal(valor: str) -> Colaborador | None:
    limpo = (valor or "").strip()
    if not limpo or limpo.lower() in {"-", "nenhum", "n/a", "na"}:
        return None
    chave = _sem_acento(limpo)
    ativos = list(Colaborador.objects.filter(ativo=True))
    for col in ativos:
        if _sem_acento(col.nome) == chave:
            return col
    for col in ativos:
        partes = _sem_acento(col.nome).split()
        if partes and partes[0] == chave.split()[0]:
            return col
    for col in ativos:
        if chave in _sem_acento(col.nome) or _sem_acento(col.nome) in chave:
            return col
    # Cria sob demanda com o nome da planilha (ex.: JOANA → Joana)
    nome_titulo = " ".join(p.capitalize() for p in limpo.split())
    return Colaborador.objects.create(nome=nome_titulo, cargo="Fiscal", ativo=True)


def _cabecalho_norm(valor) -> str:
    texto = _sem_acento(str(valor or ""))
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _localizar_colunas(cabecalho: tuple | list) -> dict[str, int] | None:
    norms = [_cabecalho_norm(c) for c in cabecalho]
    mapa: dict[str, int] = {}
    for idx, nome in enumerate(norms):
        if not nome:
            continue
        if nome in {"cod", "codigo", "codigo dominio", "dominio"} and "codigo" not in mapa:
            mapa["codigo"] = idx
        elif "cnpj" in nome and "cnpj" not in mapa:
            mapa["cnpj"] = idx
        elif nome.startswith("responsavel") and "executa" not in mapa:
            # RESPONSAVEL = quem executa
            mapa["executa"] = idx
        elif (nome.startswith("revisad") or nome.startswith("revisor")) and "revisa" not in mapa:
            mapa["revisa"] = idx
    if {"codigo", "cnpj", "executa", "revisa"} <= set(mapa):
        return mapa
    return None


def _aplicar_dados_cnpj(empresa: Empresa, dados_api: dict[str, Any]) -> dict[str, str]:
    substituicoes = dados_api.pop("substituicoes", {}) or {}
    if dados_api.get("razao_social"):
        empresa.razao_social = str(dados_api["razao_social"])[:255]
    if dados_api.get("nome_fantasia"):
        empresa.nome_fantasia = str(dados_api["nome_fantasia"])[:255]
    if dados_api.get("tipo_estabelecimento"):
        empresa.tipo_estabelecimento = str(dados_api["tipo_estabelecimento"])[:10]
    if dados_api.get("status"):
        empresa.status = str(dados_api["status"])[:20]
    if dados_api.get("data_abertura"):
        empresa.data_abertura = str(dados_api["data_abertura"])[:20]
    if dados_api.get("cep"):
        empresa.cep = somente_digitos(str(dados_api["cep"]))[:10]
    if dados_api.get("uf"):
        empresa.uf = str(dados_api["uf"]).strip().upper()[:2]
    if dados_api.get("municipio"):
        empresa.municipio = str(dados_api["municipio"])[:100]
    if dados_api.get("codigo_ibge"):
        empresa.codigo_ibge = somente_digitos(str(dados_api["codigo_ibge"]))[:7]
    if dados_api.get("logradouro"):
        empresa.logradouro = str(dados_api["logradouro"])[:255]
    if dados_api.get("numero"):
        empresa.numero = str(dados_api["numero"])[:20]
    elif not empresa.numero:
        empresa.numero = "S/N"
    if dados_api.get("complemento") is not None:
        empresa.complemento = str(dados_api.get("complemento") or "")[:100]
    if dados_api.get("bairro"):
        empresa.bairro = str(dados_api["bairro"])[:100]
    if dados_api.get("cnae_principal"):
        empresa.cnae_principal = somente_digitos(str(dados_api["cnae_principal"]))[:10]
    if dados_api.get("cnae_descricao"):
        empresa.cnae_descricao = str(dados_api["cnae_descricao"])[:255]
    if dados_api.get("ramo_atividade") and not (empresa.ramo_atividade or "").strip():
        empresa.ramo_atividade = str(dados_api["ramo_atividade"])[:50]
    if dados_api.get("inscricao_estadual"):
        empresa.inscricao_estadual = str(dados_api["inscricao_estadual"])[:30]
    return {str(uf): str(num) for uf, num in substituicoes.items()}


def _defaults_novo() -> dict[str, Any]:
    return {
        "razao_social": "Cadastro pendente (carteira Fiscal)",
        "cnae_principal": "0000000",
        "cep": "00000000",
        "logradouro": "A definir",
        "numero": "S/N",
        "bairro": "A definir",
        "uf": "SP",
        "municipio": "A definir",
        "codigo_ibge": "0000000",
        "regime_tributario": "Lucro Presumido",
        "regime_pis_cofins": "Cumulativo",
        "status": "Ativo",
        "escopo_fiscal": {item: False for item in ITENS_FISCAIS},
        "escopo_contabil": {item: False for item in ITENS_CONTABEIS},
        "escopo_dp": {item: False for item in ITENS_DP},
        "escopo_regularizacao": {item: False for item in ITENS_REGULARIZACAO},
    }


def importar_carteira_fiscal(
    arquivo_bytes: bytes,
    *,
    usuario=None,
    buscar_cnpj: bool = True,
    pausa_s: float = PAUSA_ENTRE_CONSULTAS_S,
) -> dict[str, Any]:
    garantir_equipe_fiscal()

    try:
        wb = load_workbook(io.BytesIO(arquivo_bytes), data_only=True)
    except Exception as exc:
        return {
            "ok": False,
            "criados": 0,
            "atualizados": 0,
            "erros": [f"Não foi possível abrir a planilha: {exc}"],
            "colaboradores_criados": 0,
        }

    ws = wb["EMPRESAS"] if "EMPRESAS" in wb.sheetnames else wb.worksheets[0]
    linhas = list(ws.iter_rows(values_only=True))
    if not linhas:
        return {"ok": False, "criados": 0, "atualizados": 0, "erros": ["Aba EMPRESAS vazia."]}

    colunas = None
    idx_cab = None
    for idx, row in enumerate(linhas[:10]):
        colunas = _localizar_colunas(row or ())
        if colunas:
            idx_cab = idx
            break
    if colunas is None or idx_cab is None:
        return {
            "ok": False,
            "criados": 0,
            "atualizados": 0,
            "erros": [
                "Cabeçalho não encontrado. Esperado na aba EMPRESAS: CÓD, CNPJ, RESPONSAVEL e REVISADO."
            ],
        }

    criados = 0
    atualizados = 0
    erros: list[str] = []

    for offset, row in enumerate(linhas[idx_cab + 1 :], start=idx_cab + 2):
        if not row:
            continue

        def cel(chave: str):
            i = colunas[chave]
            return row[i] if i < len(row) else None

        codigo = somente_digitos(cel("codigo"))
        cnpj = somente_digitos(cel("cnpj"))
        if cnpj and len(cnpj) < 14:
            cnpj = cnpj.zfill(14)
        if not codigo and not cnpj:
            continue
        if not codigo or len(codigo) > 4:
            erros.append(f"Linha {offset}: código Domínio inválido ('{cel('codigo')}').")
            continue
        codigo = codigo.zfill(4) if len(codigo) < 4 else codigo
        if len(cnpj) != 14 or not digitos_conferem(cnpj):
            erros.append(f"Linha {offset} (Domínio {codigo}): CNPJ inválido ('{cel('cnpj')}').")
            continue

        ops = resolver_colaborador_fiscal(str(cel("executa") or ""))
        rev = resolver_colaborador_fiscal(str(cel("revisa") or ""))
        if ops is None:
            erros.append(f"Linha {offset} (Domínio {codigo}): responsável (executa) não informado.")
            continue
        if rev is None:
            erros.append(f"Linha {offset} (Domínio {codigo}): revisador não informado.")
            continue

        empresa = (
            Empresa.objects.filter(codigo_dominio=codigo).first()
            or Empresa.objects.filter(cnpj=cnpj).first()
        )
        eh_novo = empresa is None
        antes = foto_empresa(empresa) if empresa else {}

        if eh_novo:
            if buscar_cnpj:
                try:
                    dados_api = ServicoConsultaCNPJ.dados_cadastrais(cnpj)
                except Exception as exc_api:
                    erros.append(
                        f"Linha {offset} (Domínio {codigo} · CNPJ {cnpj}): "
                        f"Buscar CNPJ falhou ({exc_api})."
                    )
                    time.sleep(pausa_s)
                    continue
                time.sleep(pausa_s)
            else:
                dados_api = {}

            empresa = Empresa(codigo_dominio=codigo, cnpj=cnpj, **_defaults_novo())
            substituicoes = _aplicar_dados_cnpj(empresa, dict(dados_api)) if dados_api else {}
            if not (empresa.razao_social or "").strip():
                empresa.razao_social = f"Cliente {codigo}"
            empresa.regime_pis_cofins = Empresa.pis_cofins_do_regime(empresa.regime_tributario) or empresa.regime_pis_cofins
        else:
            conflito_cod = Empresa.objects.filter(codigo_dominio=codigo).exclude(pk=empresa.pk).first()
            conflito_cnpj = Empresa.objects.filter(cnpj=cnpj).exclude(pk=empresa.pk).first()
            if conflito_cod or conflito_cnpj:
                erros.append(
                    f"Linha {offset}: conflito de Domínio {codigo} ou CNPJ {cnpj} com outro cliente."
                )
                continue
            empresa.codigo_dominio = codigo
            empresa.cnpj = cnpj
            substituicoes = {}

        empresa.responsavel_apuracao = ops
        empresa.responsavel_revisao = rev
        empresa.save()

        for uf_sigla, num_ie in substituicoes.items():
            num_limpo = somente_digitos(str(num_ie))
            if uf_sigla and num_limpo:
                InscricaoSubstituto.objects.update_or_create(
                    empresa=empresa,
                    uf=uf_sigla[:2].upper(),
                    defaults={"numero": num_limpo},
                )

        if eh_novo:
            try:
                criar_pastas(empresa)
            except Exception:
                pass
            criados += 1
        else:
            atualizados += 1

        if usuario is not None:
            try:
                registrar_empresa(usuario, antes, empresa)
            except Exception:
                pass

    if usuario is not None and (criados or atualizados):
        try:
            registrar(
                usuario,
                None,
                "CADASTRO",
                "Importação Carteira Fiscal",
                "",
                f"Criados: {criados} | Atualizados: {atualizados} (só CÓD/CNPJ/ops/revisão)",
            )
        except Exception:
            pass

    return {
        "ok": (criados + atualizados) > 0 or not erros,
        "criados": criados,
        "atualizados": atualizados,
        "erros": erros,
    }
