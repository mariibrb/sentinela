"""Carrega definições do checklist (cópia embutida; opcionalmente relê a pasta Manual)."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from django.conf import settings

MANUAL_PADRAO = Path(
    r"C:\Users\NASCEL-10\OneDrive - NASCEL CONTABILIDADE LTDA"
    r"\Nascel Contabilidade - publico\Fiscal\App Mari\manual nascel"
    r"\Código GITHUB e StreamLit Manual"
)


def pasta_manual_fonte() -> Path:
    bruto = (os.environ.get("SENTINELA_FECHAMENTO_SRC") or "").strip()
    if bruto:
        return Path(bruto)
    cfg = getattr(settings, "FECHAMENTO_SRC", None)
    if cfg:
        return Path(cfg)
    return MANUAL_PADRAO


def caminho_json_embutido() -> Path:
    return Path(__file__).resolve().parent / "data" / "checklists.json"


@lru_cache(maxsize=4)
def _ler_json(caminho: str) -> dict:
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def carregar_definicoes(*, forcar_fonte: bool = False) -> dict:
    """Preferência: JSON embutido no Sentinela (já sincronizado). Fonte Manual nunca é escrita."""
    embutido = caminho_json_embutido()
    if embutido.is_file() and not forcar_fonte:
        return _ler_json(str(embutido))
    # Fallback: se embutido sumiu, tenta reconstruir só em memória a partir da fonte (sem gravar nela).
    fonte = pasta_manual_fonte() / "index.html"
    if fonte.is_file():
        from fechamento.sync_manual import extrair_definicoes_do_html

        return extrair_definicoes_do_html(fonte)
    if embutido.is_file():
        return _ler_json(str(embutido))
    return {"version": 0, "regimesData": {}, "CHECKLIST_TYPES": {}, "ITEM_TYPE_MAP": {}}
