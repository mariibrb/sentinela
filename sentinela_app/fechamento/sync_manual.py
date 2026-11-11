"""Lê a pasta Manual (somente leitura) e atualiza cópias dentro do Sentinela.

NUNCA grava, renomeia ou apaga nada na pasta fonte.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fechamento.definicoes import caminho_json_embutido, pasta_manual_fonte


def extrair_definicoes_do_html(caminho_html: Path) -> dict:
    text = caminho_html.read_text(encoding="utf-8")
    a = text.find("const regimesData = ")
    b = text.find("const STORAGE_KEY")
    if a < 0 or b < 0:
        raise ValueError("Não achei regimesData/STORAGE_KEY no index.html da fonte.")
    block = text[a:b]

    regimes: dict = {}
    for m in re.finditer(r'(\w+):\s*\{\s*title:\s*"([^"]+)"\s*,\s*items:\s*\[', block):
        key, title = m.group(1), m.group(2)
        start = m.end()
        end_m = re.search(r"\]\s*\}", block[start:])
        chunk = block[start : start + end_m.start()] if end_m else ""
        items = []
        for im in re.finditer(
            r'\{\s*id:\s*"([^"]+)"\s*,\s*cat:\s*"([^"]*)"\s*,\s*title:\s*"([^"]*)"\s*,'
            r'\s*desc:\s*"([^"]*)"\s*,\s*path:\s*"([^"]*)"\s*\}',
            chunk,
        ):
            items.append(
                {
                    "id": im.group(1),
                    "cat": im.group(2),
                    "title": im.group(3),
                    "desc": im.group(4),
                    "path": im.group(5),
                }
            )
        regimes[key] = {"title": title, "items": items}

    types_block = text[text.find("const CHECKLIST_TYPES = ") : text.find("// Papéis:")]
    types = {
        tm.group(1): {"title": tm.group(2), "hint": tm.group(3)}
        for tm in re.finditer(
            r'(\w+):\s*\{\s*title:\s*"([^"]+)"\s*,\s*hint:\s*"([^"]*)"\s*\}',
            types_block,
        )
    }

    map_block = text[text.find("const ITEM_TYPE_MAP = ") : text.find("const MONTH_NAMES")]
    imap = dict(re.findall(r'(\w+)\s*:\s*"(\w+)"', map_block))

    return {
        "version": 1,
        "source": str(caminho_html.name),
        "regimesData": regimes,
        "CHECKLIST_TYPES": types,
        "ITEM_TYPE_MAP": imap,
    }


def sincronizar_do_manual(*, fonte: Path | None = None) -> dict:
    """Copia definições/assets da pasta Manual para dentro do Sentinela (só escreve no Sentinela)."""
    raiz = Path(fonte) if fonte else pasta_manual_fonte()
    if not raiz.is_dir():
        raise FileNotFoundError(f"Pasta Manual não encontrada (somente leitura): {raiz}")

    html = raiz / "index.html"
    if not html.is_file():
        html = raiz / "checklist_fiscal.html"
    if not html.is_file():
        raise FileNotFoundError(f"index.html não encontrado em {raiz}")

    # Leitura exclusiva da fonte
    definicoes = extrair_definicoes_do_html(html)

    dest_json = caminho_json_embutido()
    dest_json.parent.mkdir(parents=True, exist_ok=True)
    dest_json.write_text(json.dumps(definicoes, ensure_ascii=False, indent=2), encoding="utf-8")

    static_dir = Path(__file__).resolve().parent / "static" / "fechamento"
    static_dir.mkdir(parents=True, exist_ok=True)
    copiados = []
    for nome in ("cores_grupo_nascel.jpeg", "nascel_contabilidade.jpg.jpeg"):
        origem = raiz / nome
        if origem.is_file():
            destino = static_dir / nome
            shutil.copy2(origem, destino)  # lê origem, grava só no Sentinela
            copiados.append(nome)

    assets = raiz / "assets"
    if assets.is_dir():
        for arq in assets.iterdir():
            if arq.is_file():
                shutil.copy2(arq, static_dir / arq.name)
                copiados.append(f"assets/{arq.name}")

    # Espelho HTML só para referência/diff futuro (nunca altera a fonte)
    espelho = Path(__file__).resolve().parent / "data" / "manual_index.snapshot.html"
    espelho.write_text(html.read_text(encoding="utf-8"), encoding="utf-8")

    total_itens = sum(len(r.get("items") or []) for r in definicoes.get("regimesData", {}).values())
    return {
        "fonte": str(raiz),
        "json": str(dest_json),
        "regimes": list((definicoes.get("regimesData") or {}).keys()),
        "itens": total_itens,
        "assets": copiados,
        "snapshot": str(espelho),
    }
