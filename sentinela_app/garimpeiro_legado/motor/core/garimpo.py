"""Garimpo — arquivo fiscal + envios SIEG / Autenticidade / Contabilidade.
Excel do mês só nos botões «Atualizar e abrir Excel».
"""
from __future__ import annotations

import gc
import os
import re
import shutil
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.exportacoes import (
    MAX_AUTENTICIDADE,
    _exigir_fora_do_c,
    caminhos_novos_do_lote,
    carregar_config,
    enviar_xmls_para_sieg,
    exportar_autenticidade,
    exportar_contabilidade_zip,
)
from core.leitura_rapida import (
    carregar_chaves_indexadas,
    chave_registo_se_sha_igual,
    chaves_candidatas_dos_ficheiros,
    competencia_da_chave44,
    iterar_xmls_fonte,
    precarregar_sha_map,
)


class GarimpoInterrompido(Exception):
    """Pedido de paragem (X na fila)."""

# Acima disto: não guarda relatório completo em RAM nem remonta DataFrames do lote.
_LOTE_LEVE_FICHEIROS = 2_000
# Acima disto: não envia Autenticidade/Contabilidade automaticamente (só marca desatualizado).
_LOTE_SKIP_EXCEL_AUTO = 8_000
# SIEG: resolve/copia chaves em blocos (evita lista gigante de Path).
_SIEG_BLOCO = 500
# Leitura: menos GC, progresso por XML (não por ZIP), identify em lote.
_IDENT_BLOCO = 32
_PROG_XML_A_CADA = 8
_PROG_MIN_S = 0.2


def _prog_xml_a_cada() -> int:
    if os.environ.get("GARIMPEIRO_HEADLESS") == "1":
        return 64
    return _PROG_XML_A_CADA


def coletar_xml_zip(entradas: list[str | Path]) -> list[Path]:
    """Uma só passagem no disco (sem rglob duplicado nem sort de dezenas de milhares)."""
    out: list[Path] = []
    vistos: set[str] = set()

    def _add(c: Path) -> None:
        key = str(c).lower()
        if key not in vistos:
            vistos.add(key)
            out.append(c)

    for raw in entradas:
        p = Path(raw).expanduser()
        try:
            if not p.exists():
                continue
        except OSError:
            continue
        if p.is_file() and p.suffix.lower() in (".xml", ".zip"):
            _add(p)
        elif p.is_dir():
            for dirpath, _dirnames, filenames in os.walk(p):
                for fn in filenames:
                    low = fn.lower()
                    if low.endswith(".xml") or low.endswith(".zip"):
                        _add(Path(dirpath) / fn)
    return out


def _ficheiros_prontos(paths: list[str | Path]) -> list[Path] | None:
    """Se a janela já expandiu pastas, não volta a varrer o disco."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        try:
            if p.is_dir():
                return None
            if p.is_file() and p.suffix.lower() in (".xml", ".zip"):
                out.append(p)
            else:
                return None
        except OSError:
            return None
    return out or None


def _resolver_ficheiros(paths: list[str | Path]) -> list[Path]:
    prontos = _ficheiros_prontos(paths)
    if prontos is not None:
        return prontos
    return coletar_xml_zip([str(p) for p in paths])


def _msg_nenhum_documento(g, cnpj: str, xmls_lidos: int) -> str:
    """Erro quando o lote não indexou nada — distingue ZIP vazio de CNPJ errado."""
    import streamlit as st

    ign = int(st.session_state.get("_garimpo_skip_nao_participante") or 0)
    vis = str(cnpj or "")
    try:
        vis = g.format_cnpj_visual(cnpj)
    except Exception:
        pass
    if xmls_lidos <= 0:
        return (
            "Não foi possível ler XML neste ficheiro. "
            "Confira se o ZIP abre e contém .xml (podem estar em pastas dentro do ZIP)."
        )
    if ign:
        return (
            f"Nenhum documento da empresa selecionada ({vis}). "
            f"{ign} XML lido(s) são de outro CNPJ "
            "(matriz/filial diferentes não entram uma na outra). "
            "Selecione no Garimpeiro a empresa emitente ou destinatária dessas notas."
        )
    return (
        f"Li {xmls_lidos} XML, mas nenhum documento foi reconhecido. "
        "Confira o formato dos ficheiros."
    )


def _estimar_xmls(ficheiros: list[Path]) -> int:
    n = 0
    for p in ficheiros:
        if p.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(p) as z:
                    n += sum(1 for name in z.namelist() if name.lower().endswith(".xml"))
            except (OSError, zipfile.BadZipFile, RuntimeError):
                n += 1
        else:
            n += 1
    return max(n, 1)


def _n_workers_leitura() -> int:
    if os.environ.get("GARIMPEIRO_HEADLESS") == "1":
        return max(4, min(16, (os.cpu_count() or 4) * 2))
    return max(2, min(6, os.cpu_count() or 4))


def _ident_bloco() -> int:
    if os.environ.get("GARIMPEIRO_HEADLESS") == "1":
        return 128
    return _IDENT_BLOCO


def _enviar_sieg_por_chaves(g, cnpj: str, chaves: list[str], on_bloco=None) -> tuple[int, int]:
    """Copia para SIEG em blocos sem materializar todos os caminhos de uma vez."""
    n_ok = n_skip = 0
    total = len(chaves)
    for i in range(0, total, _SIEG_BLOCO):
        bloco = chaves[i : i + _SIEG_BLOCO]
        if on_bloco:
            on_bloco(i // _SIEG_BLOCO + 1, max((total + _SIEG_BLOCO - 1) // _SIEG_BLOCO, 1))
        paths = caminhos_novos_do_lote(g, cnpj, bloco)
        ok, skip, _dest = enviar_xmls_para_sieg(paths)
        n_ok += ok
        n_skip += skip
        del paths
    return n_ok, n_skip


def executar_garimpo(
    g,
    cnpj: str,
    paths: list[Path],
    *,
    enviar_sieg: bool | None = None,
    enviar_autenticidade: bool | None = None,
    enviar_contabilidade: bool | None = None,
    on_progress=None,
) -> tuple[bool, str, int, set[tuple[int, int]]]:
    """
    Lê XML/ZIP, indexa no arquivo fiscal partilhado.
    Lotes grandes (≥2k ficheiros): modo leve — só conta/chaves, sem relatório em RAM.
    Excel do mês/completo só pelos botões «Atualizar e abrir Excel».
    """
    import streamlit as st

    def _prog(fase: str, **kw):
        if not on_progress:
            return
        try:
            on_progress(fase=fase, **kw)
        except GarimpoInterrompido:
            raise
        except Exception:
            pass

    cfg = carregar_config()
    if enviar_sieg is None:
        enviar_sieg = bool(cfg.get("enviar_sieg_apos_garimpo", False)) and bool(
            str(cfg.get("pasta_sieg_importar") or "").strip()
        )
    if enviar_autenticidade is None:
        enviar_autenticidade = bool(cfg.get("enviar_autenticidade_apos_garimpo", False))
    if enviar_contabilidade is None:
        enviar_contabilidade = bool(cfg.get("enviar_contabilidade_apos_garimpo", False))

    st.session_state[g.SESSION_KEY_ARQUIVO_FISCAL_CAPTURA] = True
    try:
        st.session_state[g.SESSION_KEY_GARIMPO_MODO_TRABALHO] = g.GARIMPO_MODO_ARQUIVO
    except Exception:
        pass
    g._relatorio_limpar()
    st.session_state["_arquivo_fiscal_stats_novos"] = 0
    st.session_state["_garimpo_skip_nao_participante"] = 0
    st.session_state["cnpj_widget"] = g.format_cnpj_visual(cnpj)

    ficheiros = _resolver_ficheiros(paths)
    if not ficheiros:
        return False, "Nenhum XML ou ZIP na lista.", 0, set()

    total = len(ficheiros)
    modo_leve = total >= _LOTE_LEVE_FICHEIROS

    meses_tocados: set[tuple[int, int]] = set()
    # Só chaves (44 chars) — não guardar dicts completos do identify.
    chaves_lote: list[str] = []
    chaves_vistas: set[str] = set()
    erros_arq = 0
    xmls_lidos = 0
    docs_unicos = 0
    n_ja_arquivo = 0
    chaves_idx: set[str] = set()
    st.session_state["_garimpo_skip_nao_participante"] = 0

    etapas: list[str] = ["ler"]
    if enviar_sieg:
        etapas.append("sieg")
    if enviar_autenticidade:
        etapas.append("autenticidade")
    if enviar_contabilidade:
        etapas.append("contabilidade")
    etapas.append("fim")
    n_etapas = len(etapas)

    def _idx(fase: str) -> int:
        try:
            return etapas.index(fase) + 1
        except ValueError:
            return 1

    def _prog_etapa(fase: str, **kw):
        _prog(
            fase,
            etapa=_idx(fase),
            etapas_total=n_etapas,
            etapas_lista=list(etapas),
            **kw,
        )

    def _registar_chave(key: str) -> None:
        nonlocal docs_unicos
        k = str(key)
        if k not in chaves_vistas:
            chaves_vistas.add(k)
            chaves_lote.append(k)
            docs_unicos += 1

    def _ler():
        nonlocal erros_arq, xmls_lidos, n_ja_arquivo
        xml_total = xml_total_est
        ultimo_tick = 0.0

        def _tick(forcar: bool = False) -> None:
            nonlocal ultimo_tick
            agora = time.monotonic()
            if (
                not forcar
                and xmls_lidos % _prog_xml_a_cada() != 0
                and (agora - ultimo_tick) < _PROG_MIN_S
            ):
                return
            ultimo_tick = agora
            tot = max(xml_total, xmls_lidos, 1)
            extra = (
                f" · {n_ja_arquivo} já no arquivo"
                if n_ja_arquivo
                else ""
            )
            _prog_etapa(
                "ler",
                cur=xmls_lidos,
                total=tot,
                docs=docs_unicos,
                detalhe=f"A ler {xmls_lidos}/{tot} XML…{extra}",
            )

        def _aplicar(name, xml_data, fpath, res, is_p) -> None:
            nonlocal xmls_lidos
            xmls_lidos += 1
            if not res:
                del xml_data
                _tick()
                return
            if "Conteúdo" in res:
                res["Conteúdo"] = b""
            if "Conteudo" in res:
                res["Conteudo"] = b""
            key = res.get("Chave")
            if not key or g._garimpo_chave_esta_excluida(key):
                del xml_data, res
                _tick()
                return
            origem = None
            try:
                origem = g._arquivo_fiscal_fonte_origem(str(fpath))
            except Exception:
                origem = str(fpath)
            novo = bool(
                g._arquivo_fiscal_gravar_um(
                    cnpj,
                    res,
                    is_p,
                    xml_data,
                    name,
                    caminho_origem=origem,
                )
            )
            try:
                a = int(res.get("Ano") or 0)
                m = int(res.get("Mes") or 0)
                if a > 0 and 1 <= m <= 12:
                    meses_tocados.add((a, m))
            except (TypeError, ValueError):
                pass
            if novo:
                st.session_state["_arquivo_fiscal_stats_novos"] = int(
                    st.session_state.get("_arquivo_fiscal_stats_novos") or 0
                ) + 1
            try:
                ik = g._arquivo_fiscal_chave_indice(res)
                if ik:
                    chaves_idx.add(str(ik))
            except Exception:
                pass
            _registar_chave(str(key))
            del xml_data, res
            _tick()

        def _sha_map_lote() -> dict:
            import streamlit as st

            lote = st.session_state.get("_arquivo_fiscal_gravar_lote_ctx")
            if isinstance(lote, dict) and isinstance(lote.get("sha_map"), dict):
                return lote["sha_map"]
            return {}

        def _talvez_skip_sha(name: str, data: bytes) -> bool:
            nonlocal xmls_lidos, n_ja_arquivo
            ch = chave_registo_se_sha_igual(name, data, _sha_map_lote())
            if not ch:
                return False
            xmls_lidos += 1
            n_ja_arquivo += 1
            _registar_chave(ch)
            comp = competencia_da_chave44(ch)
            if comp:
                meses_tocados.add(comp)
            _tick()
            return True

        def _identificar_um(item):
            name, xml_data, fpath = item
            res, is_p = g.identify_xml_info(xml_data, cnpj, name)
            return name, xml_data, fpath, res, is_p

        workers = _n_workers_leitura()
        with ThreadPoolExecutor(max_workers=workers) as ex:
            buf: list[tuple[str, bytes, Path]] = []

            def _flush() -> None:
                if not buf:
                    return
                if len(buf) == 1:
                    name, data, fp = buf[0]
                    if not _talvez_skip_sha(name, data):
                        res, is_p = g.identify_xml_info(data, cnpj, name)
                        _aplicar(name, data, fp, res, is_p)
                else:
                    restantes: list[tuple[str, bytes, Path]] = []
                    for name, data, fp in buf:
                        if not _talvez_skip_sha(name, data):
                            restantes.append((name, data, fp))
                    for name, data, fp, res, is_p in ex.map(_identificar_um, restantes):
                        _aplicar(name, data, fp, res, is_p)
                buf.clear()

            for kind, name, payload, fpath in iterar_xmls_fonte(ficheiros, chaves_idx):
                if kind == "erro":
                    erros_arq += 1
                    continue
                if kind == "cce":
                    xmls_lidos += 1
                    _tick()
                    continue
                if kind == "skip":
                    xmls_lidos += 1
                    n_ja_arquivo += 1
                    ch = str(payload or "")
                    if ch:
                        _registar_chave(ch)
                        comp = competencia_da_chave44(ch)
                        if comp:
                            meses_tocados.add(comp)
                    _tick()
                    continue
                buf.append((name, payload, fpath))
                if len(buf) >= _ident_bloco():
                    _flush()
            _flush()
        _tick(forcar=True)

    xml_total_est = _estimar_xmls(ficheiros)
    _prog_etapa(
        "ler",
        cur=0,
        total=xml_total_est,
        detalhe=f"A abrir o arquivo e a ler {xml_total_est:,} XML…".replace(",", "."),
    )
    with g._arquivo_fiscal_gravar_lote(cnpj):
        _prog_etapa(
            "ler",
            cur=0,
            total=xml_total_est,
            detalhe="A cruzar o lote com o arquivo…",
        )
        candidatas = chaves_candidatas_dos_ficheiros(ficheiros)
        chaves_idx = carregar_chaves_indexadas(g, cnpj, candidatas)
        if hasattr(g, "_arquivo_fiscal_filtrar_chaves_só_com_xml"):
            chaves_idx = g._arquivo_fiscal_filtrar_chaves_só_com_xml(cnpj, chaves_idx)
        chaves_sha = sorted(chaves_idx) if chaves_idx else candidatas
        precarregar_sha_map(g, cnpj, chaves_sha)
        _prog_etapa(
            "ler",
            cur=0,
            total=xml_total_est,
            detalhe=f"A ler 0/{xml_total_est} XML…",
        )
        _ler()
        if (os.environ.get("GARIMPEIRO_XML_STAGING_ROOT") or "").strip():
            _prog_etapa(
                "ler",
                cur=xml_total_est,
                total=xml_total_est,
                detalhe="A enviar XML novos para o arquivo (rede)…",
            )

    gc.collect()

    if docs_unicos <= 0:
        _prog_etapa("fim", detalhe="Nenhum documento reconhecido.")
        return (
            False,
            _msg_nenhum_documento(g, cnpj, xmls_lidos),
            0,
            set(),
        )

    # Relatório em RAM só em lotes pequenos (UI Streamlit / buracos do lote).
    if not modo_leve:
        st.session_state["garimpo_ok"] = True
    else:
        st.session_state["garimpo_ok"] = True
        g._relatorio_limpar()

    n_novos = int(st.session_state.get("_arquivo_fiscal_stats_novos") or 0)
    g._arquivo_fiscal_invalidar_runtime_cache(
        cnpj, tocar_meta=False, manter_schema=True, manter_comp_resumo=False
    )
    if hasattr(g, "_arquivo_fiscal_apagar_cache_ui_disco"):
        try:
            g._arquivo_fiscal_apagar_cache_ui_disco(cnpj)
        except Exception:
            pass

    if n_novos and hasattr(g, "_arquivo_fiscal_marcar_excel_desatualizado"):
        g._arquivo_fiscal_marcar_excel_desatualizado(cnpj)

    envio_msg = ""

    if enviar_sieg:
        _prog_etapa(
            "sieg",
            detalhe=f"SIEG/Importar: a enviar XML do lote ({len(chaves_lote)})…",
        )

        def _bloco_sieg(cur_b, tot_b):
            _prog_etapa(
                "sieg",
                cur=cur_b,
                total=tot_b,
                detalhe=f"SIEG/Importar: bloco {cur_b}/{tot_b}…",
            )

        n_ok, n_skip = _enviar_sieg_por_chaves(
            g, cnpj, chaves_lote, on_bloco=_bloco_sieg
        )
        envio_msg += f" · SIEG: {n_ok} enviados"
        if n_skip:
            envio_msg += f" ({n_skip} já estavam / ignorados)"
        if not chaves_lote:
            envio_msg += " · SIEG: nenhum XML no disco para este lote"
        gc.collect()

    if enviar_autenticidade:
        meses_aut = sorted(meses_tocados)
        if not meses_aut:
            envio_msg += " · Autenticidade: nenhum mês tocado"
        elif modo_leve and total >= _LOTE_SKIP_EXCEL_AUTO:
            envio_msg += " · Autenticidade: adiada (lote grande — use o botão)"
        else:
            partes_aut: list[str] = []
            for i, (a, m) in enumerate(meses_aut, start=1):
                _prog_etapa(
                    "autenticidade",
                    cur=i,
                    total=len(meses_aut),
                    detalhe=(
                        f"Autenticidade {m:02d}/{a}: pasta completa "
                        f"({i}/{len(meses_aut)})…"
                    ),
                )
                n_ok_a, msg_a, _ = exportar_autenticidade(g, cnpj, a, m)
                if n_ok_a:
                    partes_aut.append(f"{m:02d}/{a}={n_ok_a}")
                else:
                    partes_aut.append(f"{m:02d}/{a}: {msg_a}")
                gc.collect()
            envio_msg += " · Autenticidade: " + ", ".join(partes_aut)

    if enviar_contabilidade:
        meses_ctb = sorted(meses_tocados)
        if not meses_ctb and chaves_lote:
            infer: set[tuple[int, int]] = set()
            for ch in chaves_lote:
                c = competencia_da_chave44(ch)
                if c:
                    infer.add(c)
            meses_ctb = sorted(infer)
            meses_tocados.update(infer)
        if not meses_ctb:
            envio_msg += " · Contabilidade: nenhum mês tocado"
        elif modo_leve and total >= _LOTE_SKIP_EXCEL_AUTO:
            envio_msg += " · Contabilidade: adiada (lote grande — use o botão)"
        else:
            partes_ctb: list[str] = []
            for i, (a, m) in enumerate(meses_ctb, start=1):
                _prog_etapa(
                    "contabilidade",
                    cur=i,
                    total=len(meses_ctb),
                    detalhe=(
                        f"Contabilidade {m:02d}/{a}: a gerar ZIPs "
                        f"({i}/{len(meses_ctb)})…"
                    ),
                )
                path_c, n_c, msg_c, _ = exportar_contabilidade_zip(g, cnpj, a, m)
                if path_c and n_c:
                    partes_ctb.append(f"{m:02d}/{a}={n_c} XML")
                else:
                    partes_ctb.append(f"{m:02d}/{a}: {msg_c}")
                gc.collect()
            envio_msg += " · Contabilidade: " + ", ".join(partes_ctb)

    msg = (
        f"{docs_unicos:,} documento(s) únicos · {xmls_lidos:,} XML lidos"
        f" · {n_novos:,} novo(s) no arquivo"
    ).replace(",", ".")
    if n_ja_arquivo:
        msg += f" · {n_ja_arquivo:,} já no arquivo (sem reler)".replace(",", ".")
    if modo_leve:
        msg += " · modo leve (sem relatório em RAM)"
    if erros_arq:
        msg += f" · {erros_arq} ficheiro(s) com erro de leitura"
    ignorados_fora = int(st.session_state.get("_garimpo_skip_nao_participante") or 0)
    if ignorados_fora:
        msg += (
            f" · {ignorados_fora:,} XML ignorado(s) "
            f"(empresa selecionada não é participante)"
        ).replace(",", ".")
    msg += (
        " · Excel: use «Atualizar e abrir Excel — mês» ou «completo»"
    )
    msg += envio_msg
    _prog_etapa("fim", detalhe="Concluído.", pct=1.0)
    return True, msg, docs_unicos, meses_tocados


_INVALIDOS_NOME = re.compile(r'[<>:"|?*]')


def _componente_pasta_segura(s: str) -> str:
    t = _INVALIDOS_NOME.sub("_", str(s or "").replace("\\", "_").strip())
    t = t.strip(" .") or "_"
    return t[:120]


def _destino_xml_unico(pasta: Path, nome: str) -> Path:
    nome_ok = _componente_pasta_segura(nome or "documento.xml")
    if not nome_ok.lower().endswith(".xml"):
        nome_ok += ".xml"
    out = pasta / nome_ok
    if not out.exists():
        return out
    stem, suf = out.stem, out.suffix
    i = 2
    while True:
        cand = out.with_name(f"{stem}_{i}{suf}")
        if not cand.exists():
            return cand
        i += 1


def _nome_xml_simples(g, res: dict, nome_original: str) -> str:
    if hasattr(g, "_nome_arquivo_xml_contabilidade"):
        try:
            return str(g._nome_arquivo_xml_contabilidade(res, nome_original) or "")
        except Exception:
            pass
    return str(res.get("Arquivo") or nome_original or "documento.xml")


def _slug_contab_simples(g, res: dict, is_p: bool) -> str:
    if not hasattr(g, "_pacote_contab_slug_grupo"):
        return ""
    try:
        slug = g._pacote_contab_slug_grupo(
            bool(is_p),
            res.get("Status"),
            res.get("Série") or res.get("Serie") or "0",
            res.get("Tipo") or "",
            res.get("Ano"),
            res.get("Mes"),
            res.get("Operacao"),
        )
    except Exception:
        return ""
    s = str(slug or "").strip()
    if not s:
        return ""
    return _componente_pasta_segura(s)


def _repartir_lotes_autenticidade(pasta: Path, limite: int) -> None:
    """Move XML da pasta para lote_XX_ate_{limite} (sempre, mesmo se couber num só)."""
    if limite < 1 or not pasta.is_dir():
        return
    xmls = sorted(p for p in pasta.glob("*.xml") if p.is_file())
    if not xmls:
        return
    for i in range(0, len(xmls), limite):
        parte = xmls[i : i + limite]
        lote_i = (i // limite) + 1
        dest = pasta / f"lote_{lote_i:02d}_ate_{limite}"
        dest.mkdir(parents=True, exist_ok=True)
        for src in parte:
            alvo = dest / src.name
            if alvo.exists():
                alvo = _destino_xml_unico(dest, src.name)
            try:
                src.rename(alvo)
            except OSError:
                continue


def _zipar_grupos_contab(tmp_root: Path, dest_ctb: Path) -> tuple[int, int]:
    """Um ZIP por grupo EP_/T_ na raiz da pasta Contabilidade."""
    n_xml = 0
    n_zips = 0
    if not tmp_root.is_dir():
        return 0, 0
    try:
        dest_ctb.mkdir(parents=True, exist_ok=True)
        for slug_dir in sorted(tmp_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            xmls = sorted(p for p in slug_dir.glob("*.xml") if p.is_file())
            if not xmls:
                continue
            zip_path = dest_ctb / f"{slug_dir.name}.zip"
            with zipfile.ZipFile(
                zip_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as zf:
                for xml in xmls:
                    zf.write(xml, arcname=xml.name)
                    n_xml += 1
            n_zips += 1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return n_xml, n_zips


def executar_garimpo_simples(
    g,
    cnpj: str,
    paths: list[Path],
    destino: str | Path,
    *,
    on_progress=None,
) -> tuple[bool, str, int]:
    """
    Lê e classifica XML sem gravar no ArquivoFiscal.
    Na pasta destino: Autenticidade\\lotes ≤10 mil e Contabilidade\\ZIPs EP_/T_.
    """
    import streamlit as st

    def _prog(fase: str, **kw):
        if not on_progress:
            return
        try:
            on_progress(fase=fase, **kw)
        except GarimpoInterrompido:
            raise
        except Exception:
            pass

    try:
        st.session_state[g.SESSION_KEY_GARIMPO_MODO_TRABALHO] = g.GARIMPO_MODO_SIMPLES
        st.session_state[g.SESSION_KEY_ARQUIVO_FISCAL_CAPTURA] = False
    except Exception:
        pass
    if hasattr(g, "_garimpo_aplicar_modo_simples_sem_persistencia"):
        try:
            g._garimpo_aplicar_modo_simples_sem_persistencia()
        except Exception:
            pass

    dest_raw = Path(str(destino or "").strip().strip('"').strip("'"))
    try:
        dest_in = _exigir_fora_do_c(dest_raw)
    except OSError as ex:
        return False, str(ex), 0
    if not dest_in.is_dir():
        try:
            dest_in.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            return False, f"Não foi possível criar a pasta de destino: {ex}", 0

    ficheiros = _resolver_ficheiros(paths)
    if not ficheiros:
        return False, "Nenhum XML ou ZIP na lista.", 0

    cfg = carregar_config()
    try:
        limite_aut = int(cfg.get("max_xml_autenticidade") or MAX_AUTENTICIDADE)
    except (TypeError, ValueError):
        limite_aut = MAX_AUTENTICIDADE
    if limite_aut < 1:
        limite_aut = MAX_AUTENTICIDADE

    raiz = dest_in
    raiz_aut = raiz / "Autenticidade"
    raiz_ctb = raiz / "Contabilidade"
    tmp_ctb = raiz / "_tmp_contab"
    for pasta in (raiz_aut, raiz_ctb, tmp_ctb):
        shutil.rmtree(pasta, ignore_errors=True)

    xml_total_est = _estimar_xmls(ficheiros)
    etapas = ["ler", "autenticidade", "contabilidade", "fim"]
    n_etapas = len(etapas)

    def _idx(fase: str) -> int:
        try:
            return etapas.index(fase) + 1
        except ValueError:
            return 1

    def _prog_etapa(fase: str, **kw):
        _prog(
            fase,
            etapa=_idx(fase),
            etapas_total=n_etapas,
            etapas_lista=list(etapas),
            **kw,
        )

    st.session_state["_garimpo_skip_nao_participante"] = 0
    xmls_lidos = 0
    docs_unicos = 0
    n_prop = 0
    n_terc = 0
    n_gravados = 0
    n_aut = 0
    erros_arq = 0
    chaves_vistas: set[str] = set()
    dirs_ok: set[str] = set()
    ultimo_tick = 0.0

    def _tick(forcar: bool = False) -> None:
        nonlocal ultimo_tick
        agora = time.monotonic()
        if (
            not forcar
            and xmls_lidos % _prog_xml_a_cada() != 0
            and (agora - ultimo_tick) < _PROG_MIN_S
        ):
            return
        ultimo_tick = agora
        tot = max(xml_total_est, xmls_lidos, 1)
        _prog_etapa(
            "ler",
            cur=xmls_lidos,
            total=tot,
            docs=docs_unicos,
            detalhe=f"A separar {xmls_lidos}/{tot} XML…",
        )

    def _gravar_xml(pasta: Path, nome: str, data: bytes) -> Path | None:
        nonlocal erros_arq
        try:
            ps = str(pasta)
            if ps not in dirs_ok:
                pasta.mkdir(parents=True, exist_ok=True)
                dirs_ok.add(ps)
            out = _destino_xml_unico(pasta, nome)
            out.write_bytes(data)
            return out
        except OSError:
            erros_arq += 1
            return None

    def _aplicar_simples(name, xml_data, res, is_p) -> None:
        nonlocal xmls_lidos, docs_unicos, n_prop, n_terc, n_gravados, n_aut
        xmls_lidos += 1
        if not res:
            del xml_data
            _tick()
            return
        key = str(res.get("Chave") or "")
        if key and g._garimpo_chave_esta_excluida(key):
            del xml_data, res
            _tick()
            return
        if key:
            if key in chaves_vistas:
                del xml_data, res
                _tick()
                return
            chaves_vistas.add(key)
        docs_unicos += 1
        if is_p:
            n_prop += 1
        else:
            n_terc += 1

        nome_xml = _nome_xml_simples(g, res, str(res.get("Arquivo") or name))
        gravou = False
        if _gravar_xml(raiz_aut, nome_xml, xml_data) is not None:
            n_aut += 1
            gravou = True

        slug = _slug_contab_simples(g, res, bool(is_p))
        if slug:
            pasta_slug = tmp_ctb / slug
            if _gravar_xml(pasta_slug, nome_xml, xml_data) is not None:
                gravou = True

        if gravou:
            n_gravados += 1
        del xml_data, res
        _tick()

    def _identificar_um(item):
        name, xml_data = item
        res, is_p = g.identify_xml_info(xml_data, cnpj, name)
        return name, xml_data, res, is_p

    _prog_etapa(
        "ler",
        cur=0,
        total=xml_total_est,
        detalhe="A ler e separar (Autenticidade + Contabilidade, sem arquivo)…",
    )
    workers = _n_workers_leitura()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        buf: list[tuple[str, bytes]] = []

        def _flush() -> None:
            if not buf:
                return
            if len(buf) == 1:
                name, data = buf[0]
                res, is_p = g.identify_xml_info(data, cnpj, name)
                _aplicar_simples(name, data, res, is_p)
            else:
                for name, data, res, is_p in ex.map(_identificar_um, list(buf)):
                    _aplicar_simples(name, data, res, is_p)
            buf.clear()

        for kind, name, payload, _fp in iterar_xmls_fonte(ficheiros, set()):
            if kind == "erro":
                erros_arq += 1
                continue
            if kind == "cce":
                xmls_lidos += 1
                _tick()
                continue
            if kind == "skip":
                xmls_lidos += 1
                _tick()
                continue
            buf.append((name, payload))
            if len(buf) >= _IDENT_BLOCO:
                _flush()
        _flush()
    _tick(forcar=True)

    if docs_unicos <= 0:
        for pasta in (raiz_aut, raiz_ctb, tmp_ctb):
            shutil.rmtree(pasta, ignore_errors=True)
        _prog_etapa("fim", detalhe="Nenhum documento reconhecido.")
        return (
            False,
            _msg_nenhum_documento(g, cnpj, xmls_lidos),
            0,
        )

    _prog_etapa("autenticidade", detalhe="A organizar lotes Autenticidade (até 10 mil)…")
    _repartir_lotes_autenticidade(raiz_aut, limite_aut)

    _prog_etapa("contabilidade", detalhe="A gerar ZIPs EP_/T_ da Contabilidade…")
    n_ctb_xml, n_zips = _zipar_grupos_contab(tmp_ctb, raiz_ctb)

    ignorados_fora = int(st.session_state.get("_garimpo_skip_nao_participante") or 0)
    msg = (
        f"{docs_unicos:,} documento(s) únicos · {xmls_lidos:,} XML lidos"
        f" · {n_gravados:,} separados"
        f" · própria={n_prop:,} · terceiros={n_terc:,}"
        f"\nPasta: {raiz}"
        f"\nAutenticidade: {n_aut:,} XML em lotes de até {limite_aut:,}"
        f"\nContabilidade: {n_zips:,} ZIP(s) EP_/T_, {n_ctb_xml:,} XML"
    ).replace(",", ".")
    if erros_arq:
        msg += f"\n{erros_arq} ficheiro(s) com erro de leitura/gravação"
    if ignorados_fora:
        msg += (
            f"\n{ignorados_fora:,} XML ignorado(s) "
            f"(empresa selecionada não é participante)"
        ).replace(",", ".")
    _prog_etapa("fim", detalhe="Concluído.", pct=1.0)
    return True, msg, docs_unicos
