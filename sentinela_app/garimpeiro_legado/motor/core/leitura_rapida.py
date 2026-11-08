"""Peek barato só para saltar XML já no arquivo — não classifica nem monta o Excel.

Notas novas continuam em identify_xml_info. Cancelamento e autorização da mesma
chave 44 são dois registos (44 vs 44#CANCELADOS); só se salta o tipo que já existe.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from pathlib import Path

_PEEK = 8192

_RE_CH_TAG = re.compile(
    r"<(?:chNFe|chCTe|chMDFe)>(\d{44})</",
    re.I,
)
_RE_CH_ID = re.compile(
    r"""Id=["'](?:NFe|CTe|MDFe)?(\d{44})["']""",
    re.I,
)
_RE_TP_EVENTO = re.compile(
    r"[\w:]*tpevento>\s*(\d{6})\s*</",
    re.I,
)
_RE_CSTAT = re.compile(r"<cstat>\s*(\d{3})\s*</cstat>", re.I)


def chave44_do_nome(nome: str) -> str | None:
    """Últimos 44 dígitos do nome (igual ao motor: prefixo numérico não é a chave)."""
    digitos = "".join(c for c in str(nome or "") if c.isdigit())
    if len(digitos) < 44:
        return None
    cand = digitos[-44:] if len(digitos) > 44 else digitos[:44]
    return cand if cand.isdigit() and len(cand) == 44 else None


def chave44_da_cabeca(raw: bytes) -> str | None:
    s = raw[:_PEEK].decode("utf-8", errors="ignore")
    m = _RE_CH_TAG.search(s)
    if m:
        return m.group(1)
    m = _RE_CH_ID.search(s)
    if m:
        return m.group(1)
    return None


def chave_indice(ch44: str, tipo: str) -> str | None:
    if not ch44 or len(ch44) != 44 or not ch44.isdigit():
        return None
    if tipo == "DOC":
        return ch44
    if tipo in ("CANCELADOS", "DENEGADOS", "REJEITADOS"):
        return f"{ch44}#{tipo}"
    return None


def competencia_da_chave44(chave: str) -> tuple[int, int] | None:
    """Ano/mês a partir da chave NF-e/CT-e (pos. 2–5 = AAMM)."""
    d = "".join(c for c in str(chave or "") if c.isdigit())
    if len(d) < 6:
        return None
    ch = d[-44:] if len(d) >= 44 else d[:44]
    if len(ch) < 6:
        return None
    try:
        aa = int(ch[2:4])
        mm = int(ch[4:6])
    except ValueError:
        return None
    if not (1 <= mm <= 12):
        return None
    ano = 2000 + aa if aa < 70 else 1900 + aa
    return ano, mm


def _nome_parece_so_chave44(nome: str) -> bool:
    """Nome típico de NF baixada: 312603…54501.xml (44 dígitos, sem sufixo de evento)."""
    stem = os.path.splitext(os.path.basename(str(nome or "")))[0]
    digs = "".join(c for c in stem if c.isdigit())
    if len(digs) < 44:
        return False
    ch = digs[-44:] if len(digs) > 44 else digs[:44]
    if not (len(ch) == 44 and ch.isdigit()):
        return False
    rest = re.sub(r"[\d]", "", stem)
    return rest in ("", "-", "_", ".")


def tipo_do_nome(nome: str) -> str | None:
    """Tipo óbvio pelo nome do ficheiro. Dúvida → None (não saltar)."""
    n = os.path.basename(str(nome or "")).lower()
    so_chave = _nome_parece_so_chave44(n)
    if not so_chave:
        if "110110" in n or "cartacorrecao" in n or "carta_correcao" in n:
            return "CARTA_CORRECAO"
        if "110111" in n:
            return "CANCELADOS"
    if re.search(r"(?:^|[_\-.])cce(?:[_\-.]|$)", n) or n.endswith("-cce.xml"):
        return "CARTA_CORRECAO"
    if "inut" in n:
        return "INUT"
    if "procevento" in n or "evento" in n:
        return "EVENTO"
    if "-can." in n or n.endswith("-can.xml"):
        return "CANCELADOS"
    if any(
        x in n
        for x in (
            "procnfe",
            "procnfce",
            "proccte",
            "procmdfe",
            "-nfe.xml",
            "-nfce.xml",
            "-cte.xml",
            "-mdfe.xml",
            "-procnfe.xml",
        )
    ):
        return "DOC"
    return None


def tipo_da_cabeca(raw: bytes) -> str | None:
    s = raw[:_PEEK].decode("utf-8", errors="ignore").lower()
    m_ev = _RE_TP_EVENTO.search(s)
    if m_ev:
        cod = m_ev.group(1)
        if cod == "110110":
            return "CARTA_CORRECAO"
        if cod == "110111":
            return "CANCELADOS"
        return "EVENTO_OUTRO"
    if "inutnfe" in s or "retinutnfe" in s or "procinut" in s:
        return "INUT"
    m_st = _RE_CSTAT.search(s)
    if m_st:
        st = m_st.group(1)
        if st == "110" or "deneg" in s:
            return "DENEGADOS"
        if re.match(r"30[1-9]|3[1-4]\d", st):
            return "REJEITADOS"
    if "deneg" in s:
        return "DENEGADOS"
    return "DOC"


def decidir(
    nome: str,
    chaves_idx: set[str],
    head: bytes | None = None,
) -> str:
    """
    skip | cce | peek | ler
    Conservador: qualquer dúvida → ler (identify).
    """
    tipo_nome = tipo_do_nome(nome)
    if tipo_nome == "CARTA_CORRECAO":
        return "cce"
    if tipo_nome == "INUT":
        return "ler"
    if tipo_nome == "EVENTO" and head is None:
        return "peek"
    tipo = tipo_nome
    ch44 = chave44_do_nome(nome)
    if head is not None:
        t2 = tipo_da_cabeca(head)
        if t2 == "CARTA_CORRECAO":
            return "cce"
        if t2 in ("EVENTO_OUTRO", "INUT"):
            return "ler"
        # procEvento sem tpEvento nos primeiros KB → identify (não tratar como DOC)
        if tipo_nome == "EVENTO" and t2 in (None, "DOC"):
            return "ler"
        if t2:
            tipo = t2
        elif tipo in (None, "EVENTO"):
            return "ler"
        if not ch44:
            ch44 = chave44_da_cabeca(head)
    if tipo in (None, "EVENTO"):
        return "peek" if head is None else "ler"
    if not ch44:
        return "ler"
    k = chave_indice(ch44, tipo)
    if k and k in chaves_idx:
        return "skip"
    return "ler"


def chaves_candidatas_dos_ficheiros(ficheiros: list[Path]) -> list[str]:
    """Só nomes (ZIP: namelist). Sem ler XML — 10 mil entradas é instantâneo."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(nome: str) -> None:
        base = os.path.basename(nome)
        ch = chave44_do_nome(base)
        if not ch:
            return
        tipo = tipo_do_nome(base) or "DOC"
        for k in (ch, chave_indice(ch, tipo)):
            if k and k not in seen:
                seen.add(k)
                out.append(k)

    for fpath in ficheiros:
        p = Path(fpath)
        low = p.suffix.lower()
        try:
            if low == ".zip":
                with zipfile.ZipFile(p) as z:
                    for sub in z.namelist():
                        _add(sub)
            elif low == ".xml":
                _add(p.name)
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
    return out


def _chaves_destas(conn, candidatas: list[str]) -> set[str]:
    out: set[str] = set()
    if not candidatas:
        return out
    for i in range(0, len(candidatas), 400):
        bloco = candidatas[i : i + 400]
        qs = ",".join("?" * len(bloco))
        try:
            rows = conn.execute(
                f"""
                SELECT chave, COALESCE(is_propria, 0), numero, ano, mes
                FROM xml_arquivo
                WHERE chave IN ({qs})
                """,
                bloco,
            ).fetchall()
        except Exception:
            continue
        for r in rows:
            if not r or not r[0]:
                continue
            ch = str(r[0]).strip()
            if not ch:
                continue
            if "#" in ch:
                out.add(ch)
                continue
            if os.environ.get("GARIMPEIRO_HEADLESS") == "1":
                out.add(ch)
                continue
            try:
                num = int(r[2] or 0)
            except (TypeError, ValueError):
                num = 0
            if int(r[1] or 0) == 1 and num > 0 and r[3] and r[4]:
                out.add(ch)
    return out


def carregar_chaves_indexadas(
    g, cnpj: str, candidatas: list[str] | None = None
) -> set[str]:
    """Só as chaves deste lote (PK). Nunca SELECT da tabela inteira."""
    import streamlit as st

    cx = g._arquivo_fiscal_cnpj_limpo(cnpj)
    if len(cx) != 14:
        return set()
    if not candidatas:
        return set()
    lote = st.session_state.get("_arquivo_fiscal_gravar_lote_ctx")
    conn = None
    own = False
    if isinstance(lote, dict) and lote.get("cnpj") == cx and lote.get("conn") is not None:
        conn = lote["conn"]
    else:
        try:
            db = g._arquivo_fiscal_db_path(cx)
            if not Path(db).is_file():
                return set()
            conn = g._arquivo_fiscal_conn(cx)
            own = True
        except Exception:
            return set()
    try:
        return _chaves_destas(conn, candidatas)
    except Exception:
        return set()
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def precarregar_sha_map(g, cnpj: str, candidatas: list[str]) -> None:
    """SELECT em blocos — evita identify_xml_info quando o SHA já está no índice."""
    if not candidatas:
        return
    import streamlit as st

    lote = st.session_state.get("_arquivo_fiscal_gravar_lote_ctx")
    if not isinstance(lote, dict) or lote.get("conn") is None:
        return
    cx = g._arquivo_fiscal_cnpj_limpo(cnpj)
    if lote.get("cnpj") != cx:
        return
    sm: dict = lote.setdefault("sha_map", {})
    falta = [c for c in candidatas if c and str(c) not in sm]
    if not falta:
        return
    conn = lote["conn"]
    for i in range(0, len(falta), 400):
        bloco = falta[i : i + 400]
        qs = ",".join("?" * len(bloco))
        try:
            rows = conn.execute(
                f"SELECT chave, sha256 FROM xml_arquivo WHERE chave IN ({qs})",
                bloco,
            ).fetchall()
        except Exception:
            continue
        for r in rows:
            if not r or not r[0] or not r[1]:
                continue
            sm[str(r[0])] = str(r[1])


def chave_registo_se_sha_igual(nome: str, data: bytes, sha_map: dict | None) -> str | None:
    """
    HEADLESS ou desktop: se o SHA256 do ficheiro coincide com o índice, não chama identify_xml_info.
    Devolve chave 44 para contagem; None se precisa identify.
    """
    if not isinstance(sha_map, dict) or not data:
        return None
    tipo = tipo_do_nome(nome)
    if tipo in ("INUT", "CARTA_CORRECAO"):
        return None
    head = data[:_PEEK]
    if tipo in (None, "EVENTO"):
        t2 = tipo_da_cabeca(head) if head else None
        if t2 in (None, "EVENTO_OUTRO", "INUT", "CARTA_CORRECAO"):
            return None
        if t2:
            tipo = t2
    if not tipo or tipo in ("EVENTO", "EVENTO_OUTRO"):
        return None
    ch44 = chave44_do_nome(nome) or (chave44_da_cabeca(head) if head else None)
    if not ch44:
        return None
    sha = hashlib.sha256(data).hexdigest()
    candidatos: list[str] = []
    ki = chave_indice(ch44, tipo)
    if ki:
        candidatos.append(ki)
    if ch44 not in candidatos:
        candidatos.append(ch44)
    for k in candidatos:
        if sha_map.get(k) == sha:
            return ch44
    return None


def _yield_xml_bytes(nome: str, data: bytes, origem: Path, chaves_idx: set[str]):
    d = decidir(nome, chaves_idx, data[:_PEEK] if data else None)
    if d == "skip":
        yield ("skip", nome, chave44_do_nome(nome) or chave44_da_cabeca(data), origem)
        return
    if d == "cce":
        yield ("cce", nome, None, origem)
        return
    yield ("xml", nome, data, origem)


def _iterar_zip(zf: zipfile.ZipFile, chaves_idx: set[str], origem: Path):
    for sub_nome in zf.namelist():
        if sub_nome.startswith("__MACOSX") or os.path.basename(sub_nome).startswith("."):
            continue
        if sub_nome.endswith("/") or sub_nome.endswith("\\"):
            continue
        base_sub = os.path.basename(sub_nome)
        if not base_sub:
            continue
        low = sub_nome.lower()
        if low.endswith(".zip"):
            try:
                inner = zf.read(sub_nome)
            except (zipfile.BadZipFile, OSError, RuntimeError, KeyError):
                continue
            if not inner:
                continue
            yield from _iterar_stream(io.BytesIO(inner), base_sub, chaves_idx, origem)
            continue
        if not low.endswith(".xml"):
            continue
        d = decidir(base_sub, chaves_idx)
        if d == "skip":
            yield ("skip", base_sub, chave44_do_nome(base_sub), origem)
            continue
        if d == "cce":
            yield ("cce", base_sub, None, origem)
            continue
        try:
            with zf.open(sub_nome) as fh:
                head = fh.read(_PEEK)
                d2 = decidir(base_sub, chaves_idx, head)
                if d2 == "skip":
                    ch = chave44_do_nome(base_sub) or chave44_da_cabeca(head)
                    yield ("skip", base_sub, ch, origem)
                    continue
                if d2 == "cce":
                    yield ("cce", base_sub, None, origem)
                    continue
                data = head + fh.read()
        except (OSError, KeyError, RuntimeError, zipfile.BadZipFile):
            continue
        yield ("xml", base_sub, data, origem)


def _iterar_stream(fileobj, nome_arquivo: str, chaves_idx: set[str], origem: Path):
    nome = os.path.basename(nome_arquivo)
    low = nome.lower()
    if low.endswith(".zip"):
        try:
            if hasattr(fileobj, "seek"):
                try:
                    fileobj.seek(0)
                except (OSError, io.UnsupportedOperation):
                    pass
            with zipfile.ZipFile(fileobj) as z:
                yield from _iterar_zip(z, chaves_idx, origem)
        except (zipfile.BadZipFile, OSError, RuntimeError):
            return
        return
    if not low.endswith(".xml"):
        return
    d = decidir(nome, chaves_idx)
    if d == "skip":
        yield ("skip", nome, chave44_do_nome(nome), origem)
        return
    if d == "cce":
        yield ("cce", nome, None, origem)
        return
    try:
        if hasattr(fileobj, "seek"):
            try:
                fileobj.seek(0)
            except (OSError, io.UnsupportedOperation):
                pass
        data = fileobj.read()
    except OSError:
        yield ("erro", nome, None, origem)
        return
    yield from _yield_xml_bytes(nome, data, origem, chaves_idx)


def iterar_xmls_fonte(ficheiros: list[Path], chaves_idx: set[str]):
    """
    Percorre ZIP/XML como o motor, mas não extrai entradas que já estão no índice.
    Yields: (kind, nome, payload, origem)
      skip → payload = chave 44
      cce  → payload = None
      xml  → payload = bytes
      erro → payload = None
    """
    for fpath in ficheiros:
        p = Path(fpath)
        try:
            with open(p, "rb") as fobj:
                yield from _iterar_stream(fobj, p.name, chaves_idx, p)
        except OSError:
            yield ("erro", p.name, None, p)
