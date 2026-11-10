"""Semente da carteira Fiscal: CÓD + CNPJ + quem executa + revisador.

Os cadastros foram gerados a partir da planilha do departamento Fiscal
(FISCAL - EMPRESAS E RESUMO.xlsx) e ficam em fixtures/carteira_fiscal_seed.json.
No deploy, preparar_ambiente aplica essa semente (cria/atualiza clientes e vínculos).
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from clientes.escopo import (
    ITENS_CONTABEIS,
    ITENS_DP,
    ITENS_FISCAIS,
    ITENS_REGULARIZACAO,
)
from clientes.models import Colaborador, Empresa, InscricaoSubstituto
from clientes.pastas import criar_pastas

SEED_PATH = Path(__file__).resolve().parent / "fixtures" / "carteira_fiscal_seed.json"

EQUIPE_FISCAL_PADRAO = (
    ("Giovanna", "Coordenação Fiscal"),
    ("Mariana Barbosa", "Analista Fiscal Pleno"),
    ("Clayton Bueno", "Analista Fiscal Júnior"),
    ("Ingrid", "Assistente Fiscal"),
    ("Joana", "Auxiliar Fiscal"),
    ("Paula", "Auxiliar Fiscal"),
    ("Taina Alves", "Operacional RET"),
)


def _sem_acento(texto: str) -> str:
    normalizado = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in normalizado if not unicodedata.combining(ch)).casefold().strip()


def garantir_equipe_fiscal(lista: list[dict[str, Any]] | None = None) -> None:
    origem = lista or [{"nome": n, "cargo": c, "email": "", "ativo": True} for n, c in EQUIPE_FISCAL_PADRAO]
    for item in origem:
        nome = (item.get("nome") or "").strip()
        if not nome:
            continue
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
            Colaborador.objects.create(
                nome=nome,
                cargo=(item.get("cargo") or "Fiscal")[:80],
                email=(item.get("email") or "")[:254],
                ativo=bool(item.get("ativo", True)),
            )
        else:
            mudou = False
            if not existente.ativo and item.get("ativo", True):
                existente.ativo = True
                mudou = True
            if (item.get("cargo") or "") and existente.cargo == "Analista":
                existente.cargo = str(item["cargo"])[:80]
                mudou = True
            if mudou:
                existente.save()


def resolver_colaborador_fiscal(valor: str) -> Colaborador | None:
    limpo = (valor or "").strip()
    if not limpo:
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
    return None


def _carregar_seed() -> dict[str, Any]:
    if not SEED_PATH.is_file():
        return {"colaboradores": [], "empresas": []}
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def aplicar_semente_carteira_fiscal(*, criar_pastas_clientes: bool = False) -> dict[str, int]:
    """Cria/atualiza colaboradores e clientes da carteira Fiscal (idempotente)."""
    seed = _carregar_seed()
    garantir_equipe_fiscal(seed.get("colaboradores") or None)

    criados = 0
    atualizados = 0
    for item in seed.get("empresas") or []:
        codigo = str(item.get("codigo_dominio") or "").strip()
        cnpj = "".join(ch for ch in str(item.get("cnpj") or "") if ch.isdigit())
        if not codigo or len(cnpj) != 14:
            continue

        ops = resolver_colaborador_fiscal(str(item.get("executa") or ""))
        rev = resolver_colaborador_fiscal(str(item.get("revisa") or ""))

        empresa = (
            Empresa.objects.filter(codigo_dominio=codigo).first()
            or Empresa.objects.filter(cnpj=cnpj).first()
        )
        eh_novo = empresa is None
        if eh_novo:
            empresa = Empresa(
                codigo_dominio=codigo,
                cnpj=cnpj,
                razao_social=(item.get("razao_social") or f"Cliente {codigo}")[:255],
                nome_fantasia=(item.get("nome_fantasia") or "")[:255] or None,
                tipo_estabelecimento=(item.get("tipo_estabelecimento") or "Matriz")[:10],
                status=(item.get("status") or "Ativo")[:20],
                data_abertura=(item.get("data_abertura") or "")[:20],
                cep=(item.get("cep") or "00000000")[:10],
                logradouro=(item.get("logradouro") or "A definir")[:255],
                numero=(item.get("numero") or "S/N")[:20],
                complemento=(item.get("complemento") or "")[:100] or None,
                bairro=(item.get("bairro") or "A definir")[:100],
                uf=(item.get("uf") or "SP")[:2],
                municipio=(item.get("municipio") or "A definir")[:100],
                codigo_ibge=(item.get("codigo_ibge") or "0000000")[:7],
                cnae_principal=(item.get("cnae_principal") or "0000000")[:10],
                cnae_descricao=(item.get("cnae_descricao") or "")[:255],
                ramo_atividade=(item.get("ramo_atividade") or "")[:50],
                inscricao_estadual=(item.get("inscricao_estadual") or "")[:30],
                inscricao_municipal=(item.get("inscricao_municipal") or "")[:30] or None,
                regime_tributario=item.get("regime_tributario") or "Lucro Presumido",
                regime_pis_cofins=item.get("regime_pis_cofins")
                or Empresa.pis_cofins_do_regime(item.get("regime_tributario") or "Lucro Presumido")
                or "Cumulativo",
                perfil_estabelecimento=item.get("perfil_estabelecimento") or "GERAL",
                contribuinte_icms=bool(item.get("contribuinte_icms", True)),
                contribuinte_ipi=bool(item.get("contribuinte_ipi", False)),
                escopo_fiscal={nome: False for nome in ITENS_FISCAIS},
                escopo_contabil={nome: False for nome in ITENS_CONTABEIS},
                escopo_dp={nome: False for nome in ITENS_DP},
                escopo_regularizacao={nome: False for nome in ITENS_REGULARIZACAO},
            )
        else:
            empresa.codigo_dominio = codigo
            empresa.cnpj = cnpj

        if ops is not None:
            empresa.responsavel_apuracao = ops
        if rev is not None:
            empresa.responsavel_revisao = rev
        empresa.save()

        for ie in item.get("ies_st") or []:
            uf = str(ie.get("uf") or "").strip().upper()[:2]
            numero = "".join(ch for ch in str(ie.get("numero") or "") if ch.isdigit())
            if uf and numero:
                InscricaoSubstituto.objects.update_or_create(
                    empresa=empresa,
                    uf=uf,
                    defaults={"numero": numero},
                )

        if eh_novo and criar_pastas_clientes:
            try:
                criar_pastas(empresa)
            except Exception:
                pass

        if eh_novo:
            criados += 1
        else:
            atualizados += 1

    return {"criados": criados, "atualizados": atualizados, "total_seed": len(seed.get("empresas") or [])}
