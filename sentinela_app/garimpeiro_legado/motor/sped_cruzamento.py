"""
Cruzamento SPED Fiscal (EFD ICMS/IPI) × XML do Garimpeiro.

Extrai chaves de acesso dos registros C100 (CHV_NFE) e D100 (CHV_CTE)
e associa ao mês de competência do arquivo (registro 0000, DT_INI).

Regra de vigência: por competência (MM/AAAA), o SPED **ativo** alimenta o cruzamento.
Por omissão, um novo envio do mesmo mês substitui o anterior; pode **excluir** envios
ou **ativar** outro da listagem.
O histórico fica em log + cópia do ficheiro em SPED/historico/.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Iterable

_NOME_SPED_INDICE = "sped_escrituracao.json"
_SPED_HISTORICO_SUBPASTA = "SPED/historico"
_INDICE_VERSAO = 2


@dataclass
class SpedArquivoResumo:
    nome: str
    competencia: str
    cnpj: str
    chaves: int
    linhas_c100: int = 0
    linhas_d100: int = 0


@dataclass
class SpedCadastro0000:
    """Campos do registro 0000 usados no cabeçalho do TXT Sepultador (Domínio)."""

    nome: str = ""
    cnpj: str = ""
    uf: str = ""
    ie: str = ""
    cod_mun: str = ""
    im: str = ""
    dt_ini: str = ""
    dt_fin: str = ""
    ind_perfil: str = ""


def parse_cadastro_0000_de_partes(partes: list[str]) -> SpedCadastro0000 | None:
    if len(partes) < 12:
        return None
    if str(partes[1] or "").strip().upper() != "0000":
        return None
    cx = "".join(c for c in str(partes[7] or "") if c.isdigit())[:14]
    cod_mun = "".join(c for c in str(partes[11] or "") if c.isdigit())[:7]
    perfil = ""
    if len(partes) > 14:
        perfil = str(partes[14] or "").strip().upper()[:1]
        if perfil not in ("A", "B", "C"):
            perfil = ""
    return SpedCadastro0000(
        dt_ini=str(partes[4] or "").strip(),
        dt_fin=str(partes[5] or "").strip(),
        nome=str(partes[6] or "").strip(),
        cnpj=cx,
        uf=str(partes[9] or "").strip().upper()[:2],
        ie=str(partes[10] or "").strip(),
        cod_mun=cod_mun,
        im=str(partes[12] or "").strip() if len(partes) > 12 else "",
        ind_perfil=perfil,
    )


def parse_cadastro_0000_arquivo(
    fonte: str | bytes | BinaryIO,
) -> SpedCadastro0000 | None:
    for linha in _ler_linhas_sped(fonte):
        if not linha or "|" not in linha:
            continue
        partes = _split_sped_linha(linha)
        if str(partes[1] or "").strip().upper() == "0000":
            return parse_cadastro_0000_de_partes(partes)
    return None


@dataclass
class SpedIndice:
    versao: int = _INDICE_VERSAO
    atualizado_em: str = ""
    historico: list[dict] = field(default_factory=list)
    chaves: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "versao": int(self.versao),
            "atualizado_em": self.atualizado_em,
            "historico": list(self.historico),
            "chaves": dict(self.chaves),
        }

    @classmethod
    def from_dict(cls, raw: dict | None) -> SpedIndice:
        if not isinstance(raw, dict):
            return cls()
        versao = int(raw.get("versao") or 0)
        if versao < _INDICE_VERSAO:
            return _migrar_indice_legado(raw)
        hist = raw.get("historico") or []
        if not isinstance(hist, list):
            hist = []
        ch = raw.get("chaves") or {}
        if not isinstance(ch, dict):
            ch = {}
        ind = cls(
            versao=_INDICE_VERSAO,
            atualizado_em=str(raw.get("atualizado_em") or ""),
            historico=[h for h in hist if isinstance(h, dict)],
            chaves={str(k): str(v) for k, v in ch.items() if k},
        )
        return ind


def _migrar_indice_legado(raw: dict) -> SpedIndice:
    """Converte índice antigo (arquivos + chaves acumuladas) para historico v2."""
    ind = SpedIndice()
    arquivos = raw.get("arquivos") or []
    if not isinstance(arquivos, list):
        arquivos = []
    chaves_antigas = raw.get("chaves") or {}
    agora = str(raw.get("atualizado_em") or datetime.now().isoformat(timespec="seconds"))
    if arquivos:
        for i, ar in enumerate(arquivos):
            if not isinstance(ar, dict):
                continue
            comp = str(ar.get("competencia") or "?").strip()
            ind.historico.append(
                {
                    "id": f"migrado-{i}-{comp}",
                    "importado_em": agora,
                    "nome": str(ar.get("nome") or f"sped_{i}.txt"),
                    "competencia": comp,
                    "cnpj": str(ar.get("cnpj") or ""),
                    "chaves": int(ar.get("chaves") or 0),
                    "chaves_lista": [],
                    "ativo": False,
                    "migrado": True,
                }
            )
        if ind.historico:
            ind.historico[-1]["ativo"] = True
    if isinstance(chaves_antigas, dict) and chaves_antigas:
        ind.chaves = {str(k): str(v).split(";")[0].strip() for k, v in chaves_antigas.items() if k}
    else:
        indice_sped_recalcular_chaves_vigentes(ind)
    ind.atualizado_em = agora
    return ind


def chave44_digitos(ch) -> str | None:
    d = "".join(c for c in str(ch or "") if c.isdigit())
    if len(d) >= 44:
        return d[:44]
    return None


def competencia_de_dt_ini(dt_ini: str) -> str:
    """ddmmaaaa → MM/AAAA."""
    s = "".join(c for c in str(dt_ini or "") if c.isdigit())
    if len(s) < 6:
        return ""
    if len(s) >= 8:
        mm, aa = s[2:4], s[4:8]
    else:
        mm, aa = s[:2], s[2:6]
    try:
        if int(mm) < 1 or int(mm) > 12:
            return ""
    except ValueError:
        return ""
    return f"{int(mm):02d}/{aa}"


def _competencia_de_nome_arquivo(nome: str) -> str:
    base = Path(str(nome or "")).stem
    m = re.search(r"(20\d{2})[_\-]?(\d{2})(?!\d)", base)
    if m:
        return f"{m.group(2)}/{m.group(1)}"
    m = re.search(r"(\d{2})(20\d{2})", base)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return ""


def _split_sped_linha(linha: str) -> list[str]:
    return linha.rstrip("\r\n").split("|")


def _ler_linhas_sped(fonte: str | bytes | BinaryIO) -> Iterable[str]:
    if isinstance(fonte, (bytes, bytearray)):
        texto = bytes(fonte).decode("latin-1", errors="replace")
        for ln in texto.splitlines():
            yield ln
        return
    if isinstance(fonte, str):
        for ln in fonte.splitlines():
            yield ln
        return
    for raw in fonte:
        if isinstance(raw, bytes):
            yield raw.decode("latin-1", errors="replace")
        else:
            yield str(raw)


def parse_sped_arquivo(
    fonte: str | bytes | BinaryIO,
    *,
    nome_arquivo: str = "",
) -> tuple[SpedArquivoResumo, set[str]]:
    """Lê um arquivo SPED e devolve resumo + conjunto de chaves 44 (C100/D100)."""
    competencia = ""
    cnpj = ""
    chaves: set[str] = set()
    n_c100 = n_d100 = 0

    for linha in _ler_linhas_sped(fonte):
        if not linha or "|" not in linha:
            continue
        partes = _split_sped_linha(linha)
        if len(partes) < 2:
            continue
        reg = str(partes[1] or "").strip().upper()
        if reg == "0000" and len(partes) > 5:
            if not competencia:
                competencia = competencia_de_dt_ini(partes[4])
            if len(partes) > 7:
                cx = "".join(c for c in str(partes[7] or "") if c.isdigit())
                if len(cx) >= 14:
                    cnpj = cx[:14]
            continue
        if reg == "C100" and len(partes) > 9:
            n_c100 += 1
            ch = chave44_digitos(partes[9])
            if ch:
                chaves.add(ch)
            continue
        if reg == "D100" and len(partes) > 9:
            n_d100 += 1
            ch = chave44_digitos(partes[9])
            if ch:
                chaves.add(ch)
            continue

    if not competencia:
        competencia = _competencia_de_nome_arquivo(nome_arquivo)

    resumo = SpedArquivoResumo(
        nome=str(nome_arquivo or "sped.txt"),
        competencia=competencia,
        cnpj=cnpj,
        chaves=len(chaves),
        linhas_c100=n_c100,
        linhas_d100=n_d100,
    )
    return resumo, chaves


def indice_sped_carregar(path: Path) -> SpedIndice:
    if not path.is_file():
        return SpedIndice()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return SpedIndice.from_dict(raw)
    except (OSError, ValueError, TypeError):
        return SpedIndice()


def indice_sped_gravar(path: Path, indice: SpedIndice) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    indice.versao = _INDICE_VERSAO
    indice.atualizado_em = datetime.now().isoformat(timespec="seconds")
    path.write_text(
        json.dumps(indice.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _nome_seguro_historico(nome: str, importado_em: str) -> str:
    base = Path(str(nome or "sped.txt")).name
    safe = re.sub(r'[<>:"/\\|?*]', "_", base)[:180] or "sped.txt"
    stamp = re.sub(r"[^0-9T\-]", "", str(importado_em or ""))[:19]
    return f"{stamp}_{safe}"


def _guardar_copia_sped_historico(
    pasta_empresa: Path,
    nome: str,
    importado_em: str,
    dados: bytes,
) -> str:
    """Guarda cópia do .txt em SPED/historico/ — log físico dos envios."""
    hist_dir = pasta_empresa / _SPED_HISTORICO_SUBPASTA
    hist_dir.mkdir(parents=True, exist_ok=True)
    fname = _nome_seguro_historico(nome, importado_em)
    dest = hist_dir / fname
    dest.write_bytes(dados)
    return str(dest.relative_to(pasta_empresa)).replace("\\", "/")


def indice_sped_recalcular_chaves_vigentes(indice: SpedIndice) -> None:
    """
    Mapa vigente: por competência, só o import **ativo** mais recente.
    Chave → MM/AAAA (uma competência; sem acumular meses antigos na mesma chave).
    """
    por_comp: dict[str, dict] = {}
    for h in indice.historico:
        if not h.get("ativo"):
            continue
        comp = str(h.get("competencia") or "").strip()
        if not comp or comp == "?":
            continue
        prev = por_comp.get(comp)
        if not prev or str(h.get("importado_em") or "") >= str(prev.get("importado_em") or ""):
            por_comp[comp] = h

    novo_mapa: dict[str, str] = {}
    for comp, h in por_comp.items():
        lista = h.get("chaves_lista") or []
        if not isinstance(lista, list):
            lista = []
        for ch in lista:
            k = chave44_digitos(ch)
            if k:
                novo_mapa[k] = comp
    indice.chaves = novo_mapa


def indice_sped_mesclar_arquivo(
    indice: SpedIndice,
    resumo: SpedArquivoResumo,
    chaves: set[str],
    *,
    dados_brutos: bytes | None = None,
    pasta_empresa: Path | None = None,
) -> tuple[int, bool]:
    """
    Regista um envio no histórico e actualiza o mapa vigente.
    Devolve (n_chaves, é_retificação_da_mesma_competência).
    """
    comp = str(resumo.competencia or "").strip() or "?"
    nome = str(resumo.nome or "").strip()
    agora = datetime.now().isoformat(timespec="seconds")
    import_id = f"{agora}|{nome}"

    havia_ativo = any(
        h.get("ativo") and str(h.get("competencia") or "").strip() == comp
        for h in indice.historico
    )

    for h in indice.historico:
        if h.get("ativo") and str(h.get("competencia") or "").strip() == comp:
            h["ativo"] = False
            h["substituido_em"] = agora
            h["substituido_por"] = import_id

    entry: dict = {
        "id": import_id,
        "importado_em": agora,
        "nome": nome,
        "competencia": comp,
        "cnpj": resumo.cnpj,
        "chaves": len(chaves),
        "chaves_lista": sorted(chaves),
        "c100": resumo.linhas_c100,
        "d100": resumo.linhas_d100,
        "ativo": True,
        "retificacao": bool(havia_ativo),
    }

    if dados_brutos and pasta_empresa is not None:
        try:
            entry["arquivo_guardado"] = _guardar_copia_sped_historico(
                pasta_empresa, nome, agora, dados_brutos
            )
        except OSError:
            pass

    indice.historico.append(entry)
    indice_sped_recalcular_chaves_vigentes(indice)
    return len(chaves), havia_ativo


def historico_sped_resumo(indice: SpedIndice) -> list[dict]:
    """Histórico para UI (mais recente primeiro)."""
    rows = []
    for h in reversed(indice.historico):
        rows.append(
            {
                "id": str(h.get("id") or ""),
                "importado_em": h.get("importado_em", ""),
                "nome": h.get("nome", ""),
                "competencia": h.get("competencia", ""),
                "chaves": h.get("chaves", 0),
                "ativo": bool(h.get("ativo")),
                "retificacao": bool(h.get("retificacao")),
                "arquivo_guardado": h.get("arquivo_guardado", ""),
            }
        )
    return rows


def _reativar_ultimo_restante_competencia(indice: SpedIndice, comp: str) -> None:
    """Após remover o vigente, promove o envio mais recente ainda no histórico."""
    comp = str(comp or "").strip()
    if not comp or comp == "?":
        return
    candidatos = [
        h
        for h in indice.historico
        if str(h.get("competencia") or "").strip() == comp
    ]
    if not candidatos:
        return
    for h in indice.historico:
        if str(h.get("competencia") or "").strip() == comp:
            h["ativo"] = False
    melhor = max(candidatos, key=lambda h: str(h.get("importado_em") or ""))
    melhor["ativo"] = True
    melhor.pop("substituido_em", None)
    melhor.pop("substituido_por", None)


def indice_sped_remover_entrada(
    indice: SpedIndice,
    entrada_id: str,
    *,
    pasta_empresa: Path | None = None,
    apagar_copia: bool = True,
) -> tuple[bool, str]:
    """Remove um envio do histórico e recalcula o cruzamento vigente."""
    entrada_id = str(entrada_id or "").strip()
    if not entrada_id:
        return False, "Identificador inválido."
    idx = next(
        (i for i, h in enumerate(indice.historico) if str(h.get("id") or "") == entrada_id),
        None,
    )
    if idx is None:
        return False, "Envio não encontrado no histórico."
    removido = indice.historico.pop(idx)
    comp = str(removido.get("competencia") or "").strip()
    era_ativo = bool(removido.get("ativo"))
    if apagar_copia and pasta_empresa is not None:
        rel = removido.get("arquivo_guardado")
        if rel:
            try:
                copia = pasta_empresa / str(rel).replace("\\", "/")
                if copia.is_file():
                    copia.unlink()
            except OSError:
                pass
    if era_ativo:
        _reativar_ultimo_restante_competencia(indice, comp)
    indice_sped_recalcular_chaves_vigentes(indice)
    nome = str(removido.get("nome") or "?")
    return True, f"Envio removido: `{nome}`."


def indice_sped_ativar_entrada(indice: SpedIndice, entrada_id: str) -> tuple[bool, str]:
    """Define manualmente qual envio vale para a competência dele."""
    entrada_id = str(entrada_id or "").strip()
    if not entrada_id:
        return False, "Identificador inválido."
    entrada = next(
        (h for h in indice.historico if str(h.get("id") or "") == entrada_id),
        None,
    )
    if entrada is None:
        return False, "Envio não encontrado no histórico."
    comp = str(entrada.get("competencia") or "").strip()
    if not comp or comp == "?":
        return False, "Competência desconhecida neste envio."
    agora = datetime.now().isoformat(timespec="seconds")
    for h in indice.historico:
        if str(h.get("competencia") or "").strip() != comp:
            continue
        if h is entrada:
            h["ativo"] = True
            h.pop("substituido_em", None)
            h.pop("substituido_por", None)
        elif h.get("ativo"):
            h["ativo"] = False
            h["substituido_em"] = agora
            h["substituido_por"] = entrada_id
    entrada["retificacao"] = sum(
        1 for h in indice.historico if str(h.get("competencia") or "").strip() == comp
    ) > 1
    indice_sped_recalcular_chaves_vigentes(indice)
    nome = str(entrada.get("nome") or "?")
    return True, f"**{comp}** — vigente: `{nome}`."


def indice_sped_remover_por_id(
    indice_path: Path,
    entrada_id: str,
    *,
    apagar_copia: bool = True,
) -> tuple[bool, str]:
    indice = indice_sped_carregar(indice_path)
    ok, msg = indice_sped_remover_entrada(
        indice,
        entrada_id,
        pasta_empresa=indice_path.parent,
        apagar_copia=apagar_copia,
    )
    if ok:
        indice_sped_gravar(indice_path, indice)
    return ok, msg


def indice_sped_ativar_por_id(indice_path: Path, entrada_id: str) -> tuple[bool, str]:
    indice = indice_sped_carregar(indice_path)
    ok, msg = indice_sped_ativar_entrada(indice, entrada_id)
    if ok:
        indice_sped_gravar(indice_path, indice)
    return ok, msg


def processar_sped_uploads(
    uploads: list[tuple[str, bytes]],
    indice_path: Path,
    *,
    cnpj_esperado: str | None = None,
) -> tuple[bool, str, SpedIndice]:
    """Processa SPED(s); último envio por competência vale; histórico completo."""
    if not uploads:
        return False, "Nenhum ficheiro SPED enviado.", SpedIndice()
    indice = indice_sped_carregar(indice_path)
    pasta_empresa = indice_path.parent
    cx_esp = "".join(c for c in str(cnpj_esperado or "") if c.isdigit())[:14]
    avisos: list[str] = []
    n_importados = 0
    n_retific = 0
    for nome, dados in uploads:
        if not dados:
            continue
        resumo, chaves = parse_sped_arquivo(dados, nome_arquivo=nome)
        if cx_esp and resumo.cnpj and resumo.cnpj != cx_esp:
            avisos.append(
                f"{nome}: CNPJ do SPED ({resumo.cnpj}) difere do cliente ({cx_esp})."
            )
        if not resumo.competencia:
            avisos.append(f"{nome}: competência não identificada (registo 0000).")
        if not chaves:
            avisos.append(f"{nome}: nenhuma chave C100/D100 encontrada.")
        _n, foi_ret = indice_sped_mesclar_arquivo(
            indice,
            resumo,
            chaves,
            dados_brutos=dados,
            pasta_empresa=pasta_empresa,
        )
        n_importados += 1
        if foi_ret:
            n_retific += 1

    if n_importados == 0 or not indice.chaves:
        msg = "Nenhuma chave de acesso extraída dos ficheiros SPED."
        if avisos:
            msg += "\n" + "\n".join(avisos[:5])
        return False, msg, indice

    indice_sped_gravar(indice_path, indice)
    n_vig = len(indice.chaves)
    n_hist = len(indice.historico)
    msg = (
        f"**{n_importados}** ficheiro(s) registado(s) no histórico "
        f"({n_hist} envio(s) no total). "
        f"Cruzamento vigente: **{n_vig:,}** chave(s) "
        f"(último SPED por competência).".replace(",", ".")
    )
    if n_retific:
        msg += f"\n\n**{n_retific}** retificação(ões): substituiu o SPED anterior do mesmo mês."
    if avisos:
        msg += "\n\n" + "\n".join(f"⚠ {a}" for a in avisos[:6])
    return True, msg, indice


def _ordenar_competencias_sped(comps: Iterable[str]) -> list[str]:
    """Ordena MM/AAAA cronologicamente."""

    def _key(c: str) -> tuple[int, int]:
        m = re.match(r"(\d{2})/(\d{4})", str(c or "").strip())
        if m:
            return int(m.group(2)), int(m.group(1))
        return 9999, 99

    vistos: set[str] = set()
    unicos: list[str] = []
    for c in comps:
        s = str(c or "").strip()
        if not s or s == "?" or s in vistos:
            continue
        vistos.add(s)
        unicos.append(s)
    return sorted(unicos, key=_key)


def mapa_meses_sped_historico_por_chave(indice: SpedIndice | dict | None) -> dict[str, str]:
    """
    Chave 44 → meses SPED (MM/AAAA; MM/AAAA) em que a chave apareceu em qualquer envio
    registado no histórico. Vazio = ainda não escriturada em SPED importado.
    """
    if isinstance(indice, dict):
        indice = SpedIndice.from_dict(indice)
    if not isinstance(indice, SpedIndice):
        return {}

    por_chave: dict[str, set[str]] = {}
    for h in indice.historico:
        comp = str(h.get("competencia") or "").strip()
        if not comp or comp == "?":
            continue
        lista = h.get("chaves_lista") or []
        if not isinstance(lista, list):
            continue
        for ch in lista:
            k = chave44_digitos(ch)
            if k:
                por_chave.setdefault(k, set()).add(comp)

    for k, comp in (indice.chaves or {}).items():
        k44 = chave44_digitos(k)
        c = str(comp or "").strip()
        if not k44 or not c or c == "?":
            continue
        por_chave.setdefault(k44, set()).add(c)

    return {
        k: "; ".join(_ordenar_competencias_sped(comps))
        for k, comps in por_chave.items()
        if comps
    }


def mapa_mes_sped_por_chave(indice: SpedIndice | dict | None) -> dict[str, str]:
    """Chave 44 → mês(es) SPED (histórico completo; vários meses separados por «; »)."""
    return mapa_meses_sped_historico_por_chave(indice)


def df_adicionar_coluna_mes_sped(df, mapa: dict[str, str]):
    """Adiciona coluna «Mês SPED» após «Chave» (vários meses com «; »; vazio = sem SPED)."""
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df
    if "Chave" not in df.columns:
        return df
    out = df.copy()

    def _mes(row_chave):
        k = chave44_digitos(row_chave)
        if not k:
            return ""
        return str(mapa.get(k) or "").strip()

    out["Mês SPED"] = out["Chave"].map(_mes)
    cols = [c for c in out.columns if c != "Mês SPED"]
    if "Chave" in cols:
        i = cols.index("Chave") + 1
        cols.insert(i, "Mês SPED")
    else:
        cols.append("Mês SPED")
    return out[cols]


def estatisticas_cruzamento_sped(df, mapa: dict[str, str]) -> dict:
    import pandas as pd

    if df is None or not isinstance(df, pd.DataFrame) or df.empty or "Chave" not in df.columns:
        return {"total_chave": 0, "com_sped": 0, "sem_sped": 0}
    total = com = 0
    for ch in df["Chave"].tolist():
        k = chave44_digitos(ch)
        if not k:
            continue
        total += 1
        if str(mapa.get(k) or "").strip():
            com += 1
    return {"total_chave": total, "com_sped": com, "sem_sped": max(0, total - com)}
