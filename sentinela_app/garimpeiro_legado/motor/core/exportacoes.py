"""
Modos de exportação da v2 — alinhados ao dia a dia:

1) SIEG / Domínio  → todo XML do lote ainda não presente em Importar
2) Autenticidade   → mês tocado completo → Empresa\\AAAA_MM\\própria|terceiros (≤10k)
3) Contabilidade   → mês tocado → ZIPs EP_/T_ no caminho configurado

Na partilha GARIMPEIRO o utilizador vê Autenticidade, Fechamento e Relatórios.
Arquivo (XML já lidos) fica oculto.
SIEG só grava XML se o utilizador indicar a pasta do PC dele.
Pastas internas (_sistema) exigem a senha Senhaforte@123.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from pathlib import Path

try:
    from core.pastas_layout import (
        SENHA_PASTAS_SISTEMA,
        pasta_arquivo,
        pasta_autenticidade,
        pasta_fechamento,
        pasta_relatorios,
        proteger_pastas_sistema,
        verificar_senha_pastas_sistema,
    )
except ImportError:
    from pastas_layout import (
        SENHA_PASTAS_SISTEMA,
        pasta_arquivo,
        pasta_autenticidade,
        pasta_fechamento,
        pasta_relatorios,
        proteger_pastas_sistema,
        verificar_senha_pastas_sistema,
    )

V2_RAIZ = Path(__file__).resolve().parents[1]

MAX_AUTENTICIDADE = 10_000
# Senha padrão (reset + Ajustes de pastas; só o hash fica no config)
DEFAULT_SENHA_RESET = "Senhaforte@123"

CHAVES_PASTAS_EXPORTACAO = (
    "pasta_sieg_importar",
    "pasta_autenticidade",
    "pasta_contabilidade",
)
CHAVES_PASTAS_OBRIGATORIAS = (
    "pasta_autenticidade",
    "pasta_contabilidade",
)

# Caminhos de exportação ficam por utilizador Windows (não no config partilhado da instalação).
CHAVES_CONFIG_UTILIZADOR = (
    *CHAVES_PASTAS_EXPORTACAO,
    "pasta_raiz_exportacao",
    "pastas_exportacao_configuradas",
    "dominio_inut_acumulador",
    "dominio_inut_cfop",
    "pasta_dominio_inutilizadas",
)


PARTILHA_XML_GARIMPEIRO = Path(r"\\10.10.100.201\xml\GARIMPEIRO")
PARTILHA_XML_GARIMPEIRO_LETRA = Path(r"P:\GARIMPEIRO")
_UNC_PARTILHA_XML = r"\\10.10.100.201\xml"
_UNC_GARIMPEIRO = r"\\10.10.100.201\xml\GARIMPEIRO"
_LETRA_PARTILHA_XML = "P:"
_MAPEAMENTOS_REDE_CACHE: list[tuple[str, str]] | None = None
_PASTA_DADOS_CACHE: Path | None = None


def _norm_win(s: str) -> str:
    t = str(s or "").strip().replace("/", "\\")
    if t.startswith("\\\\?\\UNC\\"):
        t = "\\" + t[7:]
    elif t.startswith("\\\\?\\"):
        t = t[4:]
    if t.startswith("\\"):
        n_bs = 0
        while n_bs < len(t) and t[n_bs] == "\\":
            n_bs += 1
        if n_bs >= 2:
            t = "\\\\" + t[n_bs:]
    while t.endswith("\\") and t not in ("\\", "\\\\"):
        t = t[:-1]
        if len(t) == 2 and t[1] == ":":
            break
    return t


def _e_unc(s: str) -> bool:
    return _norm_win(s).startswith("\\\\")


def _e_letra(s: str) -> bool:
    n = _norm_win(s)
    return len(n) >= 2 and n[1] == ":" and n[0].isalpha()


def _mapeamentos_rede() -> list[tuple[str, str]]:
    """Letra → UNC (ex.: ('P:', '\\\\10.10.100.201\\xml'))."""
    global _MAPEAMENTOS_REDE_CACHE
    if _MAPEAMENTOS_REDE_CACHE is not None:
        return _MAPEAMENTOS_REDE_CACHE
    out: list[tuple[str, str]] = []
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            fn = ctypes.windll.mpr.WNetGetConnectionW
            fn.argtypes = [
                wintypes.LPCWSTR,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            ]
            fn.restype = wintypes.DWORD
            buf = ctypes.create_unicode_buffer(1024)
            for code in range(ord("A"), ord("Z") + 1):
                letra = f"{chr(code)}:"
                n = wintypes.DWORD(1024)
                if fn(letra, buf, ctypes.byref(n)) == 0 and buf.value:
                    out.append((letra, _norm_win(buf.value)))
        except (OSError, AttributeError, ValueError):
            pass
    if not any(a.upper() == _LETRA_PARTILHA_XML for a, _ in out):
        out.append((_LETRA_PARTILHA_XML, _UNC_PARTILHA_XML))
    out.sort(key=lambda x: len(x[1]), reverse=True)
    _MAPEAMENTOS_REDE_CACHE = out
    return out


def _letra_logica_existe(letra: str) -> bool:
    """True se a letra existe no Windows (sem tocar na partilha — is_dir na rede trava)."""
    if os.name != "nt" or not letra:
        return False
    try:
        import ctypes

        mask = int(ctypes.windll.kernel32.GetLogicalDrives())
        bit = ord(letra[0].upper()) - ord("A")
        return 0 <= bit < 26 and bool(mask & (1 << bit))
    except (OSError, AttributeError, ValueError):
        return False


def caminho_letra_rede(p: str | Path) -> Path:
    """
    Mantém caminhos de rede na letra mapeada (P:\\GARIMPEIRO\\…).
    Path.resolve() no Windows converte P: em UNC e parte da app falha.
    """
    cru = str(p or "").strip()
    if not cru:
        return Path(cru)
    n = _norm_win(cru)
    if _e_unc(n):
        nl = n.lower()
        ug = _norm_win(_UNC_GARIMPEIRO).lower()
        if nl == ug or nl.startswith(ug + "\\"):
            resto = n[len(_norm_win(_UNC_GARIMPEIRO)) :]
            return Path(str(PARTILHA_XML_GARIMPEIRO_LETRA) + resto)
        ux = _norm_win(_UNC_PARTILHA_XML).lower()
        if nl == ux or nl.startswith(ux + "\\"):
            resto = n[len(_norm_win(_UNC_PARTILHA_XML)) :]
            return Path(_LETRA_PARTILHA_XML + resto)
        for letra, unc in _mapeamentos_rede():
            u = unc.lower()
            if nl == u:
                if _letra_logica_existe(letra):
                    return Path(letra + "\\")
            elif nl.startswith(u + "\\"):
                if _letra_logica_existe(letra):
                    return Path(letra + n[len(unc) :])
        return Path(n)
    return Path(n)


def _e_caminho_rede(p: str | Path) -> bool:
    s = _norm_win(str(p or ""))
    if not s:
        return False
    if _e_unc(s):
        return True
    if s.upper() == "P:" or s.upper().startswith("P:\\"):
        return True
    if _e_letra(s):
        letra = s[:2].upper()
        return any(L.upper() == letra for L, _ in _mapeamentos_rede())
    return False


def _normalizar_path_export(p: str | Path) -> Path:
    """Caminho de exportação: letra de rede, sem resolve() que gera UNC."""
    r = Path(str(p or "").strip()).expanduser() if str(p or "").strip() else Path(str(p or ""))
    r = caminho_letra_rede(r)
    if _e_caminho_rede(r):
        return r
    try:
        return r.resolve()
    except OSError:
        return r


def _mesmo_caminho(a: str | Path, b: str | Path) -> bool:
    sa = str(caminho_letra_rede(a)).replace("/", "\\").rstrip("\\").lower()
    sb = str(caminho_letra_rede(b)).replace("/", "\\").rstrip("\\").lower()
    if sa == sb:
        return True
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


_CHAVES_CAMINHO_CFG = (
    "pasta_excel",
    "pasta_raiz_exportacao",
    "pasta_sieg_importar",
    "pasta_autenticidade",
    "pasta_contabilidade",
    "pasta_dominio_inutilizadas",
)


def _normalizar_cfg_pastas(cfg: dict) -> dict:
    for k in _CHAVES_CAMINHO_CFG:
        v = str(cfg.get(k) or "").strip()
        if v:
            cfg[k] = str(caminho_letra_rede(v))
    return cfg


def _caminho_export_legado_pasta_programa(p: str | Path) -> bool:
    """
    Exportação gravada na pasta da instalação (qualquer disco).
    O instalador criava Autenticidade/Fechamento ao lado do .exe — deve ir para P:\\GARIMPEIRO.
    """
    cru = str(p or "").strip()
    if not cru:
        return False
    s = str(_normalizar_path_export(cru)).replace("/", "\\").lower().rstrip("\\")
    if s.startswith("d:\\garimpeiro"):
        return True
    try:
        inst = str(_normalizar_path_export(pasta_instalacao_garimpeiro())).replace(
            "/", "\\"
        ).lower().rstrip("\\")
    except OSError:
        inst = ""
    if not inst or not (s == inst or s.startswith(inst + "\\")):
        return False
    marcadores = (
        "\\autenticidade",
        "\\fechamento",
        "\\contabilidade",
        "\\relatórios",
        "\\relatorios",
    )
    return any(m in s for m in marcadores)


def _caminho_export_e_d_local(p: str | Path) -> bool:
    return _caminho_export_legado_pasta_programa(p)


def _partilha_garimpeiro_acessivel() -> bool:
    """P: mapeada e pasta GARIMPEIRO responde (sem travar em UNC)."""
    if not _letra_logica_existe("P"):
        return False
    try:
        return PARTILHA_XML_GARIMPEIRO_LETRA.is_dir()
    except OSError:
        return False


def _verificar_pasta_gravavel(p: str | Path) -> tuple[bool, str]:
    """Testa criar pasta e ficheiro temporário (rede P: desligada falha aqui)."""
    alvo = caminho_letra_rede(Path(str(p or "").strip()))
    if not str(alvo).strip():
        return False, "Caminho de destino vazio."
    try:
        alvo.mkdir(parents=True, exist_ok=True)
        probe = alvo / ".garimpeiro_write_test"
        probe.write_text("ok", encoding="utf-8")
        try:
            probe.unlink()
        except OSError:
            pass
        return True, ""
    except OSError as ex:
        dica = ""
        s = str(alvo).replace("/", "\\").upper()
        if s.startswith("P:\\"):
            if not _letra_logica_existe("P"):
                dica = (
                    "\n\nA unidade P: não está mapeada neste PC. "
                    "Mapeie a partilha da rede (GARIMPEIRO) antes de exportar."
                )
            elif not _partilha_garimpeiro_acessivel():
                dica = (
                    "\n\nP: existe mas P:\\GARIMPEIRO não abre. "
                    "Verifique VPN/rede e permissões de escrita."
                )
        return False, f"{ex}{dica}"


def pasta_instalacao_garimpeiro() -> Path:
    """
    Pasta raiz do programa (onde está o Garimpeiro.exe / onde o utilizador instalou).
    """
    env = (os.environ.get("GARIMPEIRO_INSTALL_ROOT") or "").strip().strip('"').strip("'")
    if env:
        return caminho_letra_rede(Path(env).expanduser())
    env_data = (os.environ.get("GARIMPEIRO_DATA_ROOT") or "").strip().strip('"').strip("'")
    if env_data and not _caminho_export_e_d_local(env_data):
        return caminho_letra_rede(Path(env_data).expanduser())

    # Instalado: {app}/app/Garimpeiro-2.0  →  {app}
    pai = V2_RAIZ.parent
    avo = pai.parent
    if (avo / "Garimpeiro.exe").is_file() or (avo / "runtime").is_dir():
        return avo.resolve()
    if (pai / "Garimpeiro.exe").is_file():
        return pai.resolve()
    return pai.resolve()


def pasta_dados_alternativa() -> Path:
    """Último recurso se a pasta de instalação não for gravável."""
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:\\")
        try:
            if not root.is_dir():
                continue
            dest = root / "Garimpeiro"
            dest.mkdir(parents=True, exist_ok=True)
            return dest.resolve()
        except OSError:
            continue
    raise OSError("Não foi possível criar a pasta de dados do Garimpeiro.")


def pasta_dados_garimpeiro() -> Path:
    """Autenticidade / Fechamento / Relatórios em P:\\GARIMPEIRO (não UNC)."""
    global _PASTA_DADOS_CACHE
    if _PASTA_DADOS_CACHE is not None:
        return _PASTA_DADOS_CACHE
    env = (os.environ.get("GARIMPEIRO_DATA_ROOT") or "").strip().strip('"').strip("'")
    if env and not _caminho_export_e_d_local(env):
        p = caminho_letra_rede(Path(env).expanduser())
        try:
            p.mkdir(parents=True, exist_ok=True)
            _PASTA_DADOS_CACHE = caminho_letra_rede(p)
            return _PASTA_DADOS_CACHE
        except OSError:
            pass
    if _letra_logica_existe("P"):
        _PASTA_DADOS_CACHE = PARTILHA_XML_GARIMPEIRO_LETRA
        return _PASTA_DADOS_CACHE
    inst = pasta_instalacao_garimpeiro()
    try:
        inst.mkdir(parents=True, exist_ok=True)
        _PASTA_DADOS_CACHE = caminho_letra_rede(inst)
        return _PASTA_DADOS_CACHE
    except OSError:
        _PASTA_DADOS_CACHE = caminho_letra_rede(pasta_dados_alternativa())
        return _PASTA_DADOS_CACHE


def _exigir_fora_do_c(p: str | Path) -> Path:
    """Normaliza o caminho escolhido pelo utilizador (letra de rede, nunca UNC)."""
    return _normalizar_path_export(p)


def _cnpj_emitente_da_chave44(chave: str) -> str:
    d = "".join(c for c in str(chave or "") if c.isdigit())
    if len(d) < 44:
        return ""
    return d[6:20]


def _cabecalho_txt_sepultador_por_empresa(meta: dict | None, ano: int, mes: int) -> list[str]:
    """Montagem do cabeçalho 0000/0001/0990 do TXT Sepultador a partir do 
    cadastro do catálogo da empresa ou de um fallback de campos gravados.
    """
    m = {} if meta is None else dict(meta)
    nome = str(m.get("nome") or m.get("razao_social") or "EMPRESA").strip()
    nome = nome.replace("|", " ").replace("\r", " ").replace("\n", " ")[:100]
    cnpj = str(m.get("cnpj") or m.get("cnpj_empresa") or "").strip()
    uf = str(m.get("uf") or m.get("estado") or "SP").strip().upper()
    ie = str(m.get("ie") or m.get("inscricao_estadual") or "").strip()
    cod_mun = str(m.get("cod_mun") or m.get("codigo_municipio") or "").strip()
    im = str(m.get("im") or m.get("inscricao_municipal") or "").strip()
    perfil = str(m.get("ind_perfil") or m.get("perfil") or "").strip().upper()[:1]
    if perfil not in ("A", "B", "C"):
        perfil = "A"

    dt_ini = date(int(ano), int(mes), 1)
    dt_fim = date(int(ano), int(mes), calendar.monthrange(int(ano), int(mes))[1])
    d0 = dt_ini.strftime("%d%m%Y")
    d1 = dt_fim.strftime("%d%m%Y")

    # Recurso: se a empresa ainda não tem cadastro completo, mantém o cabeçalho
    # funcional mínimo; a rastreabilidade do arquivo exige o bloco 0000/0001/0990.
    cnpj_txt = "".join(c for c in str(cnpj) if c.isdigit())[:14]
    if len(cnpj_txt) != 14:
        cnpj_txt = ""

    return [
        f"|0000|020|0|{d0}|{d1}|{nome}|{cnpj_txt}||{uf}|{ie}|{cod_mun}|{im}||{perfil}|0|",
        "|0001|0|",
    ]


def _contar_reg_sepultador(linhas: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in linhas:
        partes = ln.strip("|").split("|")
        if partes:
            out[partes[0]] = out.get(partes[0], 0) + 1
    return out


def _montar_texto_sepultador_sped(meta: dict, ano: int, mes: int, c100s: list[str]) -> str:
    """Bloco 0/C/9 idêntico ao scripts/piloto_sepultador_mirao.py (Domínio)."""
    cab_aberto = _cabecalho_txt_sepultador_por_empresa(meta, ano, mes)
    b0 = list(cab_aberto)
    b0.append(f"|0990|{len(b0) + 1}|")
    bc = ["|C001|0|"] + list(c100s)
    bc.append(f"|C990|{len(bc) + 1}|")
    tipos_ordem = [
        "0000",
        "0001",
        "0990",
        "C001",
        "C100",
        "C990",
        "9001",
        "9900",
        "9990",
        "9999",
    ]
    corpo_sem_9 = b0 + bc
    n_9900 = len(tipos_ordem)
    n_9990 = 1 + n_9900 + 1
    cont = _contar_reg_sepultador(corpo_sem_9)
    cont["9001"] = 1
    cont["9900"] = n_9900
    cont["9990"] = 1
    cont["9999"] = 1
    b9 = ["|9001|0|"]
    b9.extend(f"|9900|{reg}|{cont.get(reg, 0)}|" for reg in tipos_ordem)
    b9.append(f"|9990|{n_9990}|")
    todas = corpo_sem_9 + b9
    todas.append(f"|9999|{len(todas) + 1}|")
    return "\r\n".join(todas) + "\r\n"


def _xml_tag_local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _xml_find(el: ET.Element | None, nome: str) -> ET.Element | None:
    if el is None:
        return None
    for n in el.iter():
        if _xml_tag_local(n.tag) == nome:
            return n
    return None


def _xml_txt(el: ET.Element | None) -> str:
    return (el.text or "").strip() if el is not None else ""


def _ler_xml_nfe_sepultador(path: Path) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()
    inf = _xml_find(root, "infNFe")
    ide = _xml_find(root, "ide")
    emit = _xml_find(root, "emit")
    chave = ""
    if inf is not None:
        chave = "".join(c for c in (inf.attrib.get("Id") or "") if c.isdigit())[:44]
    if len(chave) != 44:
        chave = "".join(c for c in _xml_txt(_xml_find(root, "chNFe")) if c.isdigit())[:44]
    serie = _xml_txt(_xml_find(ide, "serie"))
    n = "".join(c for c in _xml_txt(_xml_find(ide, "nNF")) if c.isdigit())
    mod = _xml_txt(_xml_find(ide, "mod")) or "55"
    tp_nf = _xml_txt(_xml_find(ide, "tpNF"))
    cnpj_emit = "".join(c for c in _xml_txt(_xml_find(emit, "CNPJ")) if c.isdigit())
    try:
        ser_fmt = f"{int(serie):03d}"
    except ValueError:
        ser_fmt = (serie or "0").zfill(3)
    try:
        num = int(n)
    except ValueError:
        num = 0
    cfops: list[str] = []
    for node in root.iter():
        if _xml_tag_local(node.tag) == "CFOP":
            c4 = "".join(ch for ch in _xml_txt(node) if ch.isdigit())[:4]
            if len(c4) == 4 and c4 not in cfops:
                cfops.append(c4)
    return {
        "chave": chave,
        "serie": ser_fmt,
        "numero": num,
        "mod": mod.zfill(2)[-2:],
        "tp_nf": tp_nf,
        "cnpj_emit": cnpj_emit,
        "cfop": cfops[0] if cfops else "",
        "cfops": cfops,
    }


def _mod_sepultador(modelo) -> str:
    m = str(modelo or "").strip().upper()
    if m in ("65", "NFC-E", "NFCE"):
        return "65"
    return "55"


def _serie_sepultador(serie) -> str:
    try:
        return f"{int(serie):03d}"
    except (TypeError, ValueError):
        return str(serie or "0").zfill(3)


def _c100_dict_indice(
    k: str,
    numero,
    serie,
    modelo,
    is_propria,
    operacao,
    cnpj_empresa: str,
) -> dict:
    op = str(operacao or "").strip().upper()
    # Índice: ENTRADA / SAIDA / tpNF / códigos curtos
    if op in ("0", "E", "ENT", "ENTRADA", "ENTRADA DE MERCADORIAS"):
        tp = "0"
    elif op in ("1", "S", "SAI", "SAIDA", "SAÍDA", "SAIDA DE MERCADORIAS"):
        tp = "1"
    elif "ENTRADA" in op:
        tp = "0"
    elif "SAIDA" in op or "SAÍDA" in op:
        tp = "1"
    else:
        # Sem indício no índice → saída (legado); o XML corrige a seguir.
        tp = "1"
    if int(is_propria or 0) == 1:
        emit = cnpj_empresa
    else:
        emit = "00000000000000"
    try:
        num = int(numero or 0)
    except (TypeError, ValueError):
        num = 0
    return {
        "chave": k,
        "serie": _serie_sepultador(serie),
        "numero": num,
        "mod": _mod_sepultador(modelo),
        "tp_nf": tp,
        "cnpj_emit": emit,
    }


def _linha_c100_sepultador_de_dict(d: dict, cnpj_empresa: str) -> str:
    ind_oper = "1" if str(d.get("tp_nf") or "") != "0" else "0"
    cx = "".join(c for c in str(cnpj_empresa or "") if c.isdigit())[:14]
    emit = "".join(c for c in str(d.get("cnpj_emit") or "") if c.isdigit())[:14]
    ind_emit = "0" if emit == cx else "1"
    return _linha_txt_c100_sepultador(
        serie=d.get("serie") or "0",
        numero=d.get("numero") or 0,
        chave=str(d.get("chave") or ""),
        modelo=d.get("mod") or "55",
        ind_oper=ind_oper,
        ind_emit=ind_emit,
        cod_part="",
        cod_sit="02",
    )


def _carregar_canceladas_mes_sepultador(g, cnpj: str, ano: int, mes: int) -> list[dict]:
    itens: list[dict] = []
    conn_fn = getattr(g, "_arquivo_fiscal_conn", None)
    if callable(conn_fn):
        try:
            conn = conn_fn(cnpj)
            try:
                rows = conn.execute(
                    """
                    SELECT chave, numero, serie, modelo, is_propria, caminho_relativo,
                           nome_arquivo, operacao
                    FROM xml_arquivo
                    WHERE cnpj_empresa = ?
                      AND ano = ? AND mes = ?
                      AND upper(trim(COALESCE(status, ''))) = 'CANCELADOS'
                    ORDER BY numero, serie
                    """,
                    (cnpj, int(ano), int(mes)),
                ).fetchall()
                for row in rows:
                    itens.append(
                        {
                            "chave": row[0],
                            "numero": row[1],
                            "serie": row[2],
                            "modelo": row[3],
                            "is_propria": row[4],
                            "caminho_relativo": row[5],
                            "nome_arquivo": row[6],
                            "operacao": row[7],
                        }
                    )
            finally:
                conn.close()
        except Exception:
            itens = []
    if itens:
        return itens
    listar = getattr(g, "_arquivo_fiscal_listar_canceladas_competencia", None)
    if callable(listar):
        try:
            itens = list(listar(cnpj, int(ano), int(mes)) or [])
        except Exception:
            itens = []
    if not itens:
        tabelas = getattr(g, "_arquivo_fiscal_tabelas_status_periodo", None)
        if callable(tabelas):
            try:
                dfs = tabelas(cnpj, int(ano), [int(mes)], is_propria=1) or {}
                df = dfs.get("CANCELADOS")
                if df is not None and not getattr(df, "empty", True):
                    for _, row in df.iterrows():
                        itens.append(
                            {
                                "modelo": row.get("Modelo"),
                                "serie": row.get("Série") or row.get("Serie"),
                                "numero": row.get("Nota"),
                                "chave": row.get("Chave"),
                                "is_propria": 1,
                                "operacao": "",
                                "caminho_relativo": "",
                                "nome_arquivo": "",
                            }
                        )
            except Exception:
                itens = []
    return itens


def _linha_txt_c100_sepultador(
    *,
    serie: int | str,
    numero: int | str,
    chave: str,
    modelo: str | None = None,
    ind_oper: str = "1",
    ind_emit: str = "0",
    cod_part: str = "",
    cod_sit: str = "02",
) -> str:
    """Linha C100 do desenho inicial do Sepultador: 20 campos vazios após a chave,
    juros e trabalho iguais ao piloto Mirão, para que o arquivo fique no leiaute
    de referência consumido pelo processamento de Domínio.
    """
    try:
        ser = int(str(serie or "0").strip() or "0")
        ser_txt = f"{ser:03d}"
    except Exception:
        ser_txt = str(str(serie or "0").strip() or "0").zfill(3)
    try:
        num = int(str(numero or "0").strip() or "0")
        num_txt = str(num)
    except Exception:
        num_txt = str(numero or "0").strip()
    mod = str(modelo or "55").strip().upper()
    if mod in ("65", "NFC-E", "NFCE"):
        mod = "65"
    elif mod not in ("55", "65"):
        mod = "55"
    campos = [
        "C100",
        str(ind_oper or "1"),
        str(ind_emit or "0"),
        str(cod_part or ""),
        str(mod or "55"),
        str(cod_sit or "02"),
        ser_txt,
        num_txt,
        str(chave or "").strip(),
    ]
    campos.extend([""] * 20)
    return "|" + "|".join(campos) + "|"


def _ler_cadastro_0000_de_ficheiro_sped(caminho: Path) -> dict:
    try:
        import sped_cruzamento as sc

        cad = sc.parse_cadastro_0000_arquivo(caminho.read_bytes())
    except OSError:
        return {}
    if cad is None:
        return {}
    return {
        "nome": cad.nome,
        "cnpj": cad.cnpj,
        "uf": cad.uf,
        "ie": cad.ie,
        "cod_mun": cad.cod_mun,
        "im": cad.im,
        "ind_perfil": getattr(cad, "ind_perfil", "") or "",
    }


def _preencher_meta_sepultador_de_dict(meta: dict, src: dict) -> None:
    if not src:
        return
    if str(src.get("nome") or src.get("razao_social") or "").strip():
        meta["nome"] = str(src.get("nome") or src.get("razao_social") or "").strip()
    for k in ("uf", "ie", "cod_mun", "im", "ind_perfil"):
        v = str(src.get(k) or "").strip()
        if v:
            if k == "uf":
                meta[k] = v.upper()[:2]
            elif k == "ind_perfil":
                p = v.upper()[:1]
                if p in ("A", "B", "C"):
                    meta[k] = p
            else:
                meta[k] = v
    if str(src.get("cod_mun") or "").strip():
        meta["cod_mun"] = "".join(c for c in str(src.get("cod_mun")) if c.isdigit())[:7]


def _validar_cabecalho_sepultador(meta: dict) -> tuple[bool, str]:
    cx = "".join(c for c in str(meta.get("cnpj") or "") if c.isdigit())[:14]
    faltas: list[str] = []
    if len(cx) != 14:
        faltas.append("CNPJ da empresa (14 dígitos)")
    nome = str(meta.get("nome") or "").strip()
    if not nome or nome.upper() == "EMPRESA":
        faltas.append(
            "Razão social (nome curto ou campo «Razão social SPED» no catálogo)"
        )
    uf = str(meta.get("uf") or "").strip().upper()
    if len(uf) != 2:
        faltas.append("UF (sigla do estado, 2 letras)")
    ie = str(meta.get("ie") or "").strip()
    if not ie:
        faltas.append("Inscrição estadual (IE — use ISENTO se aplicável)")
    cm = "".join(c for c in str(meta.get("cod_mun") or "") if c.isdigit())
    if len(cm) != 7:
        faltas.append("Código IBGE do município (7 dígitos, ex.: 3550308)")
    perfil = str(meta.get("ind_perfil") or meta.get("perfil") or "").strip().upper()[:1]
    if perfil not in ("A", "B", "C"):
        faltas.append("Perfil SPED (A ou B)")
    if not faltas:
        return True, ""
    texto = (
        "O Domínio rejeita o TXT se o bloco 0000 estiver incompleto ou diferente "
        "do cadastro da empresa.\n\nFalta preencher:\n• "
        + "\n• ".join(faltas)
        + "\n\nNo Garimpeiro: menu Empresas → seleccione a empresa → "
        "«Cadastro SPED (Domínio)» → «Preencher do SPED» ou digite manualmente → "
        "«Guardar alterações»."
    )
    return False, texto


def _meta_empresa_sepultador(
    g, cnpj: str, ano: int | None = None, mes: int | None = None
) -> dict:
    """Cadastro do bloco 0000: catálogo → SPED importado → defaults vazios."""
    meta: dict = {
        "cnpj": str(cnpj or "").strip(),
        "nome": "",
        "uf": "",
        "ie": "",
        "cod_mun": "",
        "im": "",
        "ind_perfil": "",
    }
    try:
        from app import _arquivo_fiscal_cnpj_limpo, _arquivo_fiscal_carregar_catalogo
    except Exception:
        return meta

    try:
        cnp = _arquivo_fiscal_cnpj_limpo(cnpj)
    except Exception:
        cnp = str(cnpj or "").strip()
    meta["cnpj"] = cnp
    cat = None
    try:
        if callable(_arquivo_fiscal_carregar_catalogo):
            cat = _arquivo_fiscal_carregar_catalogo()
    except Exception:
        cat = None
    if isinstance(cat, dict):
        info = cat.get(cnp, {}) if isinstance(info := cat.get(cnp, {}), dict) else {}
        if isinstance(info, dict):
            razao = str(info.get("razao_social") or "").strip()
            curto = str(info.get("nome") or "").strip()
            meta["nome"] = razao or curto
            meta["uf"] = str(info.get("uf") or info.get("estado") or "").strip().upper()
            meta["ie"] = str(info.get("ie") or info.get("inscricao_estadual") or "").strip()
            meta["cod_mun"] = str(
                info.get("cod_mun")
                or info.get("cod_municipio")
                or info.get("codigo_municipio")
                or ""
            ).strip()
            meta["im"] = str(info.get("im") or info.get("inscricao_municipal") or "").strip()
            perfil = str(info.get("ind_perfil") or info.get("perfil") or "").strip().upper()[:1]
            if perfil in ("A", "B", "C"):
                meta["ind_perfil"] = perfil

    precisa = not (
        meta.get("nome")
        and len(str(meta.get("uf") or "")) == 2
        and str(meta.get("ie") or "").strip()
        and len("".join(c for c in str(meta.get("cod_mun") or "") if c.isdigit())) == 7
        and str(meta.get("ind_perfil") or "").upper()[:1] in ("A", "B", "C")
    )
    if precisa and g is not None:
        path_fn = getattr(g, "_arquivo_fiscal_caminho_sped_vigente_competencia", None)
        if callable(path_fn) and ano is not None and mes is not None:
            try:
                p = path_fn(cnp, int(ano), int(mes))
                if p and Path(p).is_file():
                    _preencher_meta_sepultador_de_dict(
                        meta, _ler_cadastro_0000_de_ficheiro_sped(Path(p))
                    )
            except Exception:
                pass
        if (
            not str(meta.get("ie") or "").strip()
            or len("".join(c for c in str(meta.get("cod_mun") or "") if c.isdigit())) != 7
        ):
            try:
                import sped_cruzamento as sc

                ind_fn = getattr(g, "_sped_indice_path", None)
                pasta_fn = getattr(g, "_arquivo_fiscal_pasta_empresa", None)
                if callable(ind_fn) and callable(pasta_fn):
                    ind = sc.indice_sped_carregar(ind_fn(cnp))
                    pasta = Path(pasta_fn(cnp))
                    for h in reversed(ind.historico or []):
                        if not h.get("ativo"):
                            continue
                        rel = str(h.get("arquivo_guardado") or "").strip()
                        if not rel:
                            continue
                        px = (pasta / rel.replace("\\", "/")).resolve()
                        if px.is_file():
                            _preencher_meta_sepultador_de_dict(
                                meta, _ler_cadastro_0000_de_ficheiro_sped(px)
                            )
                            break
            except Exception:
                pass
    return meta


def exportar_txt_sepultador_canceladas(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    *,
    destino: str | Path | None = None,
    respeitar_entrada_saida: bool = False,
) -> tuple[str | None, int, str]:
    """Gera TXT SPED Sepultador (C100 sit.02) das canceladas do mês.

    respeitar_entrada_saida=False (botão padrão): tudo como saída (1) — legado.
    respeitar_entrada_saida=True (botão teste): tpNF do XML / operação no índice
    (0=entrada, 1=saída).
    """
    try:
        from app import _arquivo_fiscal_cnpj_limpo, _arquivo_fiscal_conn
    except Exception:
        _arquivo_fiscal_cnpj_limpo = None
        _arquivo_fiscal_conn = None

    cnpj = str(cnpj or "").strip()
    if cnpj and callable(_arquivo_fiscal_cnpj_limpo):
        try:
            cnpj = _arquivo_fiscal_cnpj_limpo(cnpj)
        except Exception:
            pass

    cx_emp = "".join(c for c in str(cnpj or "") if c.isdigit())[:14]

    itens = _carregar_canceladas_mes_sepultador(g, cnpj, int(ano), int(mes))
    raiz_emp: Path | None = None
    pasta_fn = getattr(g, "_arquivo_fiscal_pasta_empresa", None)
    if callable(pasta_fn):
        try:
            raiz_emp = Path(pasta_fn(cnpj))
        except Exception:
            raiz_emp = None

    por_chave: dict[str, list[dict]] = {}
    for it in itens:
        k = "".join(c for c in str(it.get("chave") or it.get("Chave") or "") if c.isdigit())[:44]
        if len(k) != 44:
            continue
        por_chave.setdefault(k, []).append(it)

    linhas: list[str] = []
    ignoradas_cnpj: list[str] = []
    erros_xml: list[str] = []

    def _path_xml_nfe(k: str, lst: list[dict]) -> Path | None:
        if raiz_emp is None or not raiz_emp.is_dir():
            return None
        cands: list[tuple[str, str]] = []
        for it in lst:
            cands.append(
                (
                    str(it.get("caminho_relativo") or it.get("Caminho") or ""),
                    str(it.get("nome_arquivo") or it.get("Arquivo") or ""),
                )
            )
        for rel, nome in cands:
            if not rel and not nome:
                continue
            rel_s = str(rel or "").replace("/", "\\")
            path = raiz_emp / rel_s if rel_s else None
            if path and path.is_file() and "cancelamento" not in path.name.lower():
                return path
            if nome:
                p2 = (path.parent / nome) if path else (raiz_emp / str(nome))
                if p2.is_file() and "cancelamento" not in p2.name.lower():
                    return p2
        # Fallback: nome com a chave de 44 dígitos no Arquivo (XML autorizado).
        try:
            for p in raiz_emp.rglob(f"*{k}*.xml"):
                if not p.is_file():
                    continue
                low = p.name.lower()
                if "cancelamento" in low or "evento" in low:
                    continue
                return p
        except OSError:
            pass
        return None

    vistos: set[str] = set()
    n_entrada = 0
    n_saida = 0
    for k, lst in por_chave.items():
        if k in vistos:
            continue
        if len(cx_emp) == 14:
            cx_ch = _cnpj_emitente_da_chave44(k)
            if cx_ch and cx_ch != cx_emp:
                ignoradas_cnpj.append(k)
                continue
        vistos.add(k)
        pref = [r for r in lst if "#" not in str(r.get("chave") or "")] or lst
        it0 = pref[0]
        d = _c100_dict_indice(
            k,
            it0.get("numero") or it0.get("Nota"),
            it0.get("serie") or it0.get("Série") or it0.get("Serie"),
            it0.get("modelo") or it0.get("Modelo"),
            it0.get("is_propria"),
            it0.get("operacao"),
            cx_emp,
        )
        path_xml = _path_xml_nfe(k, lst)
        if path_xml:
            try:
                dx = _ler_xml_nfe_sepultador(path_xml)
                if dx.get("chave") in ("", k):
                    if dx.get("numero"):
                        d["numero"] = dx["numero"]
                    if dx.get("serie"):
                        d["serie"] = dx["serie"]
                    if dx.get("mod") in ("55", "65"):
                        d["mod"] = dx["mod"]
                    # tpNF do XML manda (corrige entrada/saída sem editar o TXT).
                    if dx.get("tp_nf") in ("0", "1"):
                        d["tp_nf"] = dx["tp_nf"]
                    if dx.get("cnpj_emit"):
                        d["cnpj_emit"] = dx["cnpj_emit"]
            except Exception as ex:
                erros_xml.append(f"{k}: {ex}")
        # Botão padrão (legado): força tudo saída — não mexer no comportamento antigo.
        if not respeitar_entrada_saida:
            d["tp_nf"] = "1"
        if not d.get("numero") or not d.get("chave"):
            continue
        if str(d.get("tp_nf") or "") == "0":
            n_entrada += 1
        else:
            n_saida += 1
        linhas.append(_linha_c100_sepultador_de_dict(d, cx_emp))

    if not linhas:
        if ignoradas_cnpj:
            return (
                None,
                0,
                f"Nenhuma cancelada exportada: {len(ignoradas_cnpj)} chave(s) com emitente "
                f"diferente do CNPJ da empresa ({cx_emp}). Confira emissão própria e catálogo.",
            )
        return None, 0, f"Nenhuma cancelada no mês {int(mes):02d}/{int(ano)}."

    if destino:
        dest = _exigir_fora_do_c(Path(destino))
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        emp = "empresa"
        try:
            emp = str(getattr(g, "_rotulo_empresa_pasta", lambda c, a: "empresa")(g, cnpj))
        except Exception:
            emp = "empresa"
        dest_dir = _exigir_fora_do_c(pasta_dados_garimpeiro() / "ArquivoFiscal" / "Sepultador" / emp)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"SPED_CANCELADAS_{cnpj}_{ano}{mes:02d}.txt"

    meta = _meta_empresa_sepultador(g, cnpj, int(ano), int(mes))
    if len(cx_emp) == 14:
        meta["cnpj"] = cx_emp
    ok_cab, err_cab = _validar_cabecalho_sepultador(meta)
    if not ok_cab:
        return None, 0, err_cab
    texto = _montar_texto_sepultador_sped(meta, int(ano), int(mes), linhas)
    dest.write_bytes(texto.encode("windows-1252", errors="replace"))
    if respeitar_entrada_saida:
        msg = (
            f"[TESTE entrada/saída] {len(linhas)} cancelada(s) → {dest}\n"
            f"Entrada: {n_entrada} · Saída: {n_saida} "
            f"(C100 campo 2: 0=entrada, 1=saída — do XML quando possível)."
        )
    else:
        msg = (
            f"{len(linhas)} cancelada(s) → {dest}\n"
            f"Modo padrão: todas como saída (1) — use o botão teste para entrada/saída."
        )
    if erros_xml:
        msg += f"\n\n{len(erros_xml)} XML(s) não lidos — usei dados do índice."
    if ignoradas_cnpj:
        msg += (
            f"\n\n{len(ignoradas_cnpj)} chave(s) omitida(s): emitente na chave ≠ CNPJ "
            f"da empresa ({cx_emp}) — típico de nota de terceiros ou filial errada."
        )
    return str(dest), len(linhas), msg


def exportar_txt_sepultador_canceladas_teste_entrada_saida(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    *,
    destino: str | Path | None = None,
) -> tuple[str | None, int, str]:
    """Mesmo SPED Sepultador do botão padrão, mas com entrada/saída correctas no C100."""
    return exportar_txt_sepultador_canceladas(
        g,
        cnpj,
        int(ano),
        int(mes),
        destino=destino,
        respeitar_entrada_saida=True,
    )


def _instalacao_detectada(base: Path | None = None) -> bool:
    root = base or pasta_instalacao_garimpeiro()
    return (root / "Garimpeiro.exe").is_file() or (root / "runtime").is_dir()


def raiz_exportacao_padrao() -> Path:
    """Raiz da partilha GARIMPEIRO (irmã de Autenticidade / Fechamento / Relatórios)."""
    return pasta_dados_garimpeiro()


def garantir_pastas_exportacao_padrao() -> dict:
    """
    Garante Autenticidade / Fechamento / Relatórios na partilha.
    Arquivo interno fica oculto.
    SIEG não é criado: só grava se o utilizador indicar a pasta do PC.
    """
    cfg = carregar_config()
    raiz = Path(str(cfg.get("pasta_raiz_exportacao") or "").strip() or raiz_exportacao_padrao())
    vis = proteger_pastas_sistema(raiz)
    cfg["pasta_raiz_exportacao"] = str(raiz)
    cfg["pasta_autenticidade"] = vis["autenticidade"]
    cfg["pasta_contabilidade"] = vis["fechamento"]
    cfg["pasta_excel"] = vis["relatorios"]
    cfg["pastas_exportacao_configuradas"] = True
    guardar_config(cfg)
    return cfg


def nome_utilizador_windows() -> str:
    """Utilizador Windows atual (pastas de exportação são por utilizador)."""
    for chave in ("USERNAME", "USER"):
        v = (os.environ.get(chave) or "").strip()
        if v:
            return v
    return "default"


def pasta_dados_utilizador() -> Path:
    """Config de exportação por utilizador — dentro da pasta de dados."""
    base = pasta_dados_garimpeiro() / "_utilizadores" / nome_utilizador_windows()
    base.mkdir(parents=True, exist_ok=True)
    return caminho_letra_rede(base)


def caminho_config_utilizador() -> Path:
    return pasta_dados_utilizador() / "config_v2.json"


def caminho_config() -> Path:
    """
    Config partilhado da instalação (flags, senha) — pasta de dados.
    Caminhos SIEG/Autenticidade/Contabilidade ficam em caminho_config_utilizador().
    """
    dados = pasta_dados_garimpeiro()
    prefer = dados / "config_v2.json"
    env_data = (os.environ.get("GARIMPEIRO_DATA_ROOT") or "").strip().strip('"').strip("'")
    if env_data or _instalacao_detectada():
        return prefer
    legado = V2_RAIZ / "config_v2.json"
    if legado.is_file():
        return legado
    return prefer


def _sem_chaves_utilizador(cfg: dict) -> dict:
    out = dict(cfg)
    for k in CHAVES_CONFIG_UTILIZADOR:
        out.pop(k, None)
    out.pop("pasta_excel", None)
    return out


def _extrair_chaves_utilizador(cfg: dict) -> dict:
    return {k: cfg[k] for k in CHAVES_CONFIG_UTILIZADOR if k in cfg}


def _pastas_validas_em_cfg(cfg: dict) -> bool:
    caminhos = [str(cfg.get(k) or "").strip() for k in CHAVES_PASTAS_OBRIGATORIAS]
    return all(caminhos)


def _migrar_pastas_partilhadas_para_utilizador(cfg_maquina: dict) -> None:
    """
    Versões antigas gravavam pastas no config partilhado da instalação.
    Copia uma vez para o utilizador Windows atual; outros utilizadores não herdam.
    """
    user_path = caminho_config_utilizador()
    user_cfg = _ler_json_config(user_path)
    if _pastas_validas_em_cfg(user_cfg) and bool(
        user_cfg.get("pastas_exportacao_configuradas")
    ):
        return

    if not _pastas_validas_em_cfg(cfg_maquina):
        return

    owner = str(cfg_maquina.get("pastas_utilizador") or "").strip()
    user = nome_utilizador_windows()
    if owner and owner.upper() != user.upper():
        return

    for k in CHAVES_CONFIG_UTILIZADOR:
        if k in cfg_maquina:
            user_cfg[k] = cfg_maquina[k]
    user_cfg["pastas_exportacao_configuradas"] = True
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(
        json.dumps(user_cfg, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    limpo = _sem_chaves_utilizador(cfg_maquina)
    limpo["pastas_utilizador"] = user
    path_maq = caminho_config()
    path_maq.parent.mkdir(parents=True, exist_ok=True)
    path_maq.write_text(
        json.dumps(limpo, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def hash_senha_reset(senha: str) -> str:
    return hashlib.sha256(str(senha or "").encode("utf-8")).hexdigest()


def _ler_json_config(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def carregar_config() -> dict:
    raiz = pasta_dados_garimpeiro()
    cfg: dict = {
        "pasta_excel": str(pasta_relatorios(raiz)),
        "pasta_raiz_exportacao": str(raiz),
        "pasta_sieg_importar": "",
        "pasta_autenticidade": str(pasta_autenticidade(raiz)),
        "pasta_contabilidade": str(pasta_fechamento(raiz)),
        "pastas_exportacao_configuradas": False,
        "enviar_sieg_apos_garimpo": False,
        "enviar_autenticidade_apos_garimpo": False,
        "enviar_contabilidade_apos_garimpo": False,
        "limpar_exportacoes_ultimo_dia_mes": True,
        "ultima_limpeza_exportacoes": "",
        "max_xml_autenticidade": MAX_AUTENTICIDADE,
        "senha_reset_arquivo_sha256": hash_senha_reset(DEFAULT_SENHA_RESET),
    }

    cfg_maquina = _ler_json_config(caminho_config())
    _migrar_pastas_partilhadas_para_utilizador(cfg_maquina)
    cfg_maquina = _ler_json_config(caminho_config())

    # Mescla defaults do pacote + config partilhado (sem pastas de exportação)
    for path in (V2_RAIZ / "config_v2.json",):
        data = _ler_json_config(path)
        for k, v in data.items():
            if k not in CHAVES_CONFIG_UTILIZADOR:
                cfg[k] = v
    for k, v in _sem_chaves_utilizador(cfg_maquina).items():
        cfg[k] = v

    cfg_utilizador = _ler_json_config(caminho_config_utilizador())
    for k, v in cfg_utilizador.items():
        if k in CHAVES_CONFIG_UTILIZADOR or k in CHAVES_PASTAS_EXPORTACAO:
            cfg[k] = v
    _normalizar_cfg_pastas(cfg)

    raiz = pasta_dados_garimpeiro()
    legado_export = (
        _caminho_export_legado_pasta_programa(cfg.get("pasta_raiz_exportacao") or "")
        or _caminho_export_legado_pasta_programa(cfg.get("pasta_autenticidade") or "")
        or _caminho_export_legado_pasta_programa(cfg.get("pasta_contabilidade") or "")
    )
    if legado_export:
        cfg["pasta_raiz_exportacao"] = str(raiz)
        cfg["pasta_autenticidade"] = str(pasta_autenticidade(raiz, criar=True))
        cfg["pasta_contabilidade"] = str(pasta_fechamento(raiz, criar=True))
        cfg["pasta_excel"] = str(pasta_relatorios(raiz, criar=True))

    sieg_u = str(cfg.get("pasta_sieg_importar") or "").strip()
    try:
        from core.pastas_layout import pasta_sieg_e_da_partilha
    except ImportError:
        from pastas_layout import pasta_sieg_e_da_partilha
    if sieg_u and pasta_sieg_e_da_partilha(sieg_u, raiz):
        cfg["pasta_sieg_importar"] = ""

    raiz_p = caminho_letra_rede(Path(str(cfg.get("pasta_raiz_exportacao") or "").strip() or raiz))
    cfg["pasta_excel"] = str(pasta_relatorios(raiz_p))
    try:
        Path(cfg["pasta_excel"]).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    if not str(cfg.get("pasta_autenticidade") or "").strip():
        cfg["pasta_autenticidade"] = str(pasta_autenticidade(raiz_p))
    if not str(cfg.get("pasta_contabilidade") or "").strip():
        cfg["pasta_contabilidade"] = str(pasta_fechamento(raiz_p))

    ok = all(str(cfg.get(k) or "").strip() for k in CHAVES_PASTAS_OBRIGATORIAS)
    cfg["pastas_exportacao_configuradas"] = ok

    if cfg["pastas_exportacao_configuradas"]:
        for key in CHAVES_PASTAS_OBRIGATORIAS:
            try:
                Path(str(cfg[key])).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        sieg_cfg = str(cfg.get("pasta_sieg_importar") or "").strip()
        if sieg_cfg:
            try:
                Path(sieg_cfg).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    if not cfg.get("senha_reset_arquivo_sha256"):
        cfg["senha_reset_arquivo_sha256"] = hash_senha_reset(DEFAULT_SENHA_RESET)
    cfg = _normalizar_cfg_pastas(cfg)
    if legado_export:
        try:
            guardar_config(cfg)
        except OSError:
            pass
    return cfg


def pastas_exportacao_configuradas(cfg: dict | None = None) -> bool:
    cfg = cfg if cfg is not None else carregar_config()
    if not bool(cfg.get("pastas_exportacao_configuradas")):
        return False
    caminhos = [str(cfg.get(k) or "").strip() for k in CHAVES_PASTAS_OBRIGATORIAS]
    if not all(caminhos):
        return False
    return True


def _defaults_pastas_instalacao() -> dict[str, str]:
    """Fallback se o config ainda não tiver caminhos (partilha GARIMPEIRO)."""
    raiz = pasta_dados_garimpeiro()
    return {
        "pasta_sieg_importar": "",
        "pasta_autenticidade": str(pasta_autenticidade(raiz)),
        "pasta_contabilidade": str(pasta_fechamento(raiz)),
        "pasta_excel": str(pasta_relatorios(raiz)),
    }


def _raiz_comum_tres_pastas(sieg: str, aut: str, cont: str) -> str:
    try:
        p1, p2, p3 = (
            caminho_letra_rede(sieg),
            caminho_letra_rede(aut),
            caminho_letra_rede(cont),
        )
        a, b, c = str(p1.parent).lower(), str(p2.parent).lower(), str(p3.parent).lower()
        if a == b == c:
            return str(p1.parent)
    except (OSError, ValueError):
        pass
    return ""


def sugerir_pastas_exportacao() -> dict[str, str]:
    """Caminhos visíveis na partilha + SIEG só se o utilizador já gravou o dele."""
    raiz = pasta_dados_garimpeiro()
    vis = proteger_pastas_sistema(raiz)
    data: dict = _ler_json_config(caminho_config_utilizador())
    sieg = str(data.get("pasta_sieg_importar") or "").strip()
    return {
        "pasta_raiz_exportacao": str(raiz),
        "pasta_sieg_importar": sieg,
        "pasta_autenticidade": vis["autenticidade"],
        "pasta_contabilidade": vis["fechamento"],
        "pasta_excel": vis["relatorios"],
        "pasta_arquivo": vis["arquivo"],
        "origem": "partilha",
    }


def aplicar_raiz_exportacao(raiz: str | Path) -> dict:
    """Autenticidade / Fechamento / Relatórios / Arquivo na pasta-raiz da partilha."""
    r = _exigir_fora_do_c(raiz)
    vis = proteger_pastas_sistema(r)
    cfg = carregar_config()
    cfg["pasta_raiz_exportacao"] = str(r)
    cfg["pasta_autenticidade"] = vis["autenticidade"]
    cfg["pasta_contabilidade"] = vis["fechamento"]
    cfg["pasta_excel"] = vis["relatorios"]
    cfg["pastas_exportacao_configuradas"] = True
    guardar_config(cfg)
    return cfg


def aplicar_pastas_exportacao(
    *,
    pasta_sieg: str | Path,
    pasta_autenticidade: str | Path,
    pasta_contabilidade: str | Path,
    pasta_raiz: str | Path | None = None,
) -> dict:
    """Grava os três caminhos de exportação (após permissão por senha)."""
    cfg = carregar_config()
    cfg["pasta_sieg_importar"] = (
        str(_exigir_fora_do_c(pasta_sieg)) if str(pasta_sieg or "").strip() else ""
    )
    cfg["pasta_autenticidade"] = str(_exigir_fora_do_c(pasta_autenticidade))
    cfg["pasta_contabilidade"] = str(_exigir_fora_do_c(pasta_contabilidade))
    if pasta_raiz is not None and str(pasta_raiz).strip():
        cfg["pasta_raiz_exportacao"] = str(_exigir_fora_do_c(pasta_raiz))
    cfg["pastas_exportacao_configuradas"] = True
    for key in CHAVES_PASTAS_OBRIGATORIAS:
        Path(str(cfg[key])).mkdir(parents=True, exist_ok=True)
    sieg = str(cfg.get("pasta_sieg_importar") or "").strip()
    if sieg:
        Path(sieg).mkdir(parents=True, exist_ok=True)
    guardar_config(cfg)
    return cfg


def verificar_senha_reset(senha: str, cfg: dict | None = None) -> bool:
    if verificar_senha_pastas_sistema(senha):
        return True
    cfg = cfg if cfg is not None else carregar_config()
    esperado = str(cfg.get("senha_reset_arquivo_sha256") or "")
    if not esperado:
        esperado = hash_senha_reset(DEFAULT_SENHA_RESET)
    return hash_senha_reset(senha) == esperado


def definir_senha_reset(nova_senha: str) -> None:
    cfg = carregar_config()
    cfg["senha_reset_arquivo_sha256"] = hash_senha_reset(nova_senha)
    guardar_config(cfg)


def guardar_config(cfg: dict) -> None:
    cfg = dict(cfg)
    _normalizar_cfg_pastas(cfg)
    cfg["pasta_excel"] = str(pasta_relatorios(pasta_dados_garimpeiro()))

    user_part = _extrair_chaves_utilizador(cfg)
    if _pastas_validas_em_cfg(user_part):
        user_part["pastas_exportacao_configuradas"] = True

    if user_part:
        user_path = caminho_config_utilizador()
        user_path.parent.mkdir(parents=True, exist_ok=True)
        existente = _ler_json_config(user_path)
        existente.update(user_part)
        user_path.write_text(
            json.dumps(existente, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    machine_path = caminho_config()
    machine_path.parent.mkdir(parents=True, exist_ok=True)
    machine_cfg = _ler_json_config(machine_path)
    for k in CHAVES_CONFIG_UTILIZADOR:
        machine_cfg.pop(k, None)
    machine_cfg.pop("pasta_excel", None)
    machine_cfg.update(_sem_chaves_utilizador(cfg))
    if _pastas_validas_em_cfg(user_part):
        machine_cfg["pastas_utilizador"] = nome_utilizador_windows()
    try:
        machine_path.write_text(
            json.dumps(machine_cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _chave_mes_anterior(hoje: date | None = None) -> str:
    d = hoje or date.today()
    if d.month == 1:
        return f"{d.year - 1:04d}-12"
    return f"{d.year:04d}-{d.month - 1:02d}"


def _chave_mes_limpeza_exportacoes(hoje: date | None = None) -> str:
    """
    No último dia do mês → chave desse mês (limpa no fecho).
    Nos outros dias → chave do mês anterior (recuperação se não abriram no último dia).
    """
    d = hoje or date.today()
    ultimo = calendar.monthrange(d.year, d.month)[1]
    if d.day == ultimo:
        return f"{d.year:04d}-{d.month:02d}"
    return _chave_mes_anterior(d)


def _esvaziar_conteudo_pasta(pasta: Path) -> tuple[int, int]:
    """Remove ficheiros e subpastas; mantém a pasta raiz. Devolve (ok, falhas)."""
    n_ok = n_fail = 0
    if not pasta.is_dir():
        return 0, 0
    try:
        itens = list(pasta.iterdir())
    except OSError:
        return 0, 1
    for item in itens:
        try:
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=False)
            else:
                item.unlink(missing_ok=True)
            n_ok += 1
        except OSError:
            n_fail += 1
    return n_ok, n_fail


def _pasta_e_partilha_garimpeiro(pasta: Path) -> bool:
    try:
        s = str(Path(pasta).expanduser()).replace("/", "\\").strip().lower()
    except OSError:
        return False
    if s.startswith("\\\\"):
        return True
    return s.startswith("p:\\garimpeiro") or s.startswith("p:/garimpeiro")


def _pasta_limpeza_mensal_local(pasta: Path) -> bool:
    """Limpeza automática só nas pastas locais da instalação — nunca na partilha XML."""
    if not pasta or not str(pasta).strip():
        return False
    if _pasta_e_partilha_garimpeiro(pasta):
        return False
    try:
        p = caminho_letra_rede(Path(pasta).expanduser())
        raiz = caminho_letra_rede(pasta_dados_garimpeiro())
        return str(p).lower().startswith(str(raiz).lower())
    except OSError:
        return False


def limpar_pastas_autenticidade_contabilidade(
    *,
    forcar: bool = False,
) -> tuple[bool, str]:
    """
    Esvazia Autenticidade e Contabilidade (não toca em SIEG/Importar nem ArquivoFiscal).
    Se forcar=False, só corre quando a chave do ciclo mensal ainda não foi limpa.
    Na 1.ª ativação (sem histórico), não apaga a meio do mês — só marca a linha de base.
    """
    cfg = carregar_config()
    if not forcar and not bool(cfg.get("limpar_exportacoes_ultimo_dia_mes", True)):
        return False, ""

    chave = _chave_mes_limpeza_exportacoes()
    ultima = str(cfg.get("ultima_limpeza_exportacoes") or "").strip()
    if not forcar and ultima == chave:
        return False, ""

    # 1.ª vez com a função: evita apagar pacotes a meio do mês após atualizar.
    if not forcar and not ultima:
        hoje = date.today()
        ultimo = calendar.monthrange(hoje.year, hoje.month)[1]
        if hoje.day != ultimo:
            cfg["ultima_limpeza_exportacoes"] = chave
            guardar_config(cfg)
            return False, ""

    aut = Path(str(cfg.get("pasta_autenticidade") or "").strip())
    cont = Path(str(cfg.get("pasta_contabilidade") or "").strip())
    if not str(aut) and not str(cont):
        return False, "Pastas Autenticidade/Contabilidade não configuradas."

    alvos = [("Autenticidade", aut), ("Contabilidade", cont)]
    locais = [
        (rotulo, pasta)
        for rotulo, pasta in alvos
        if pasta and str(pasta).strip() and _pasta_limpeza_mensal_local(pasta)
    ]
    if not locais:
        if not forcar:
            cfg["ultima_limpeza_exportacoes"] = chave
            guardar_config(cfg)
        return False, ""

    partes: list[str] = []
    total_ok = total_fail = 0
    for rotulo, pasta in locais:
        ok, fail = _esvaziar_conteudo_pasta(pasta)
        total_ok += ok
        total_fail += fail
        try:
            pasta.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        partes.append(f"{rotulo}: {ok} item(ns) removido(s)")
        if fail:
            partes.append(f"({fail} falha(s) em {rotulo})")

    cfg["ultima_limpeza_exportacoes"] = chave
    guardar_config(cfg)

    msg = (
        "Pacotes Autenticidade e Contabilidade foram limpos "
        f"(ciclo {chave}).\n"
        + " · ".join(partes)
        + "\n\nSe precisar de novo, gere o(s) mês(es) pelos botões do Garimpeiro.\n"
        "ArquivoFiscal e SIEG/Importar não foram alterados."
    )
    if total_ok == 0 and total_fail == 0:
        msg = (
            f"Limpeza mensal ({chave}): pastas já estavam vazias.\n"
            "ArquivoFiscal e SIEG/Importar não foram alterados."
        )
    return True, msg


def verificar_limpeza_exportacoes_no_arranque() -> tuple[bool, str]:
    """Chamado ao abrir o app — limpeza automática se devido."""
    return limpar_pastas_autenticidade_contabilidade(forcar=False)


def _nome_xml_seguro(nome: str, chave: str) -> str:
    n = Path(str(nome or "").strip()).name
    if n.lower().endswith(".xml") and n:
        return n
    ch = "".join(c for c in str(chave or "") if c.isdigit())
    if len(ch) >= 44:
        return f"{ch[:44]}.xml"
    return n or "documento.xml"


def enviar_xmls_para_sieg(
    caminhos: list[Path],
    *,
    destino: str | Path | None = None,
) -> tuple[int, int, str]:
    """
    Copia XML soltos para a pasta Importar do Cofre SIEG.
    Se o ficheiro já existe lá (mesmo nome e tamanho), não copia de novo.
    """
    dest_s = str(
        destino
        if destino is not None
        else (carregar_config().get("pasta_sieg_importar") or "")
    ).strip()
    if not dest_s:
        return 0, 0, "Indique em Ajustes a pasta SIEG deste PC. Sem esse caminho o Garimpeiro não grava XML para o SIEG."
    dest = Path(dest_s)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return 0, 0, f"Não foi possível criar/abrir {dest}: {ex}"
    ok = 0
    skip = 0
    for src in caminhos:
        p = Path(src)
        if not p.is_file() or p.suffix.lower() != ".xml":
            skip += 1
            continue
        alvo = dest / p.name
        try:
            if alvo.is_file() and alvo.stat().st_size == p.stat().st_size:
                skip += 1
                continue
            shutil.copy2(p, alvo)
            ok += 1
        except OSError:
            skip += 1
    return ok, skip, str(dest)


def exportar_sieg_mes(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    pasta_destino: str | Path | None = None,
    *,
    pular_se_atualizado: bool = False,
) -> tuple[int, int, str, bool]:
    """
    Envia todos os XML do mês no arquivo para SIEG/Importar
    (salta se já existir com o mesmo nome e tamanho).
    Devolve (enviados, saltados, mensagem, fez_update).
    """
    dest_s = str(
        pasta_destino
        if pasta_destino is not None
        else (carregar_config().get("pasta_sieg_importar") or "")
    ).strip()
    if not dest_s:
        return (
            0,
            0,
            "Indique em Ajustes a pasta SIEG deste PC. Sem esse caminho o Garimpeiro não grava XML para o SIEG.",
            False,
        )
    dest = Path(dest_s)
    paths: list[Path] = []
    for it in _itens_mes(g, cnpj, ano, mes):
        p = _path_item(g, cnpj, it)
        if p is not None:
            paths.append(p)
    if not paths:
        return 0, 0, f"Nenhum XML do mês {mes:02d}/{ano} no arquivo para enviar.", False

    if pular_se_atualizado and _sieg_esta_atualizado(paths, dest):
        return (
            0,
            len(paths),
            f"SIEG já atualizado ({len(paths)} XML) — a abrir pasta.",
            False,
        )

    ok, skip, dest_s = enviar_xmls_para_sieg(paths, destino=dest)
    fez = ok > 0
    if fez:
        msg = (
            f"SIEG/Importar: {ok} enviados, {skip} já existiam "
            f"(mês {mes:02d}/{ano}) → {dest_s}"
        )
    else:
        msg = f"SIEG já atualizado ({skip} XML) — a abrir pasta."
    return ok, skip, msg, fez


def _sieg_esta_atualizado(paths: list[Path], dest: Path) -> bool:
    if not dest.is_dir() or not paths:
        return False
    for p in paths:
        try:
            alvo = dest / p.name
            if not alvo.is_file() or alvo.stat().st_size != p.stat().st_size:
                return False
        except OSError:
            return False
    return True


def _itens_mes(g, cnpj: str, ano: int, mes: int) -> list[dict]:
    return g._arquivo_fiscal_listar(cnpj, ano=int(ano), mes=int(mes)) or []


def _path_item(g, cnpj: str, it: dict) -> Path | None:
    rel = str(it.get("caminho_relativo") or "").replace("/", "\\")
    if not rel:
        return None
    p = g._arquivo_fiscal_pasta_empresa(cnpj) / rel
    return p if p.is_file() else None


def _sanit_parte_pasta(s: str, max_len: int) -> str:
    t = str(s or "").strip()
    for a in "()[]{}":
        t = t.replace(a, " ")
    t = t.replace("—", " ").replace("–", " ")
    out: list[str] = []
    for c in t:
        if c.isspace() or c in "._-":
            out.append("_")
        elif c.isalnum():
            out.append(c)
    r = "".join(out)
    while "__" in r:
        r = r.replace("__", "_")
    return r.strip("._-")[:max_len]


def nome_pasta_empresa_padrao(cnpj: str, *, codigo: str = "", nome: str = "") -> str:
    """Pasta visível: 578_AFFONSO_DESCARTÁVEIS_LTDA_RET_45785442000390."""
    cx = _cnpj_digitos_export(cnpj)
    partes = [p for p in (_sanit_parte_pasta(codigo, 16), _sanit_parte_pasta(nome, 50)) if p]
    if len(cx) == 14:
        return "_".join(partes + [cx]) if partes else cx
    return "_".join(partes) if partes else "empresa"


def cnpj_de_nome_pasta(nome: str) -> str:
    """CNPJ de 14 dígitos no nome da pasta (no fim, ou pasta só com o número)."""
    s = str(nome or "").strip()
    if not s:
        return ""
    m = re.search(r"(\d{14})\s*$", s)
    if m:
        return m.group(1)
    compact = re.sub(r"[\s.\-_/]", "", s)
    if len(compact) == 14 and compact.isdigit():
        return compact
    digs = "".join(c for c in s if c.isdigit())
    if len(digs) == 14:
        return digs
    return ""


def _nome_catalogo_export(g, cnpj: str) -> str:
    cx = _cnpj_digitos_export(cnpj)
    try:
        info = (g._arquivo_fiscal_carregar_catalogo() or {}).get(cx, {}) or {}
        return str(info.get("nome") or info.get("razao_social") or "").strip()
    except Exception:
        return ""


def _rotulo_empresa_pasta(g, cnpj: str) -> str:
    return nome_pasta_empresa_padrao(
        cnpj,
        codigo=_codigo_catalogo_export(g, cnpj),
        nome=_nome_catalogo_export(g, cnpj),
    )


def _cnpj_digitos_export(cnpj: str) -> str:
    return "".join(c for c in str(cnpj or "") if c.isdigit())[:14]


def _codigo_catalogo_export(g, cnpj: str) -> str:
    cx = _cnpj_digitos_export(cnpj)
    if len(cx) != 14:
        return ""
    try:
        info = (g._arquivo_fiscal_carregar_catalogo() or {}).get(cx, {}) or {}
        return str(info.get("codigo") or "").strip()
    except Exception:
        return ""


def _nome_pasta_cnpj_sozinho(cnpj: str) -> list[str]:
    """Variantes de pasta quando só havia o CNPJ (sem razão social)."""
    cx = _cnpj_digitos_export(cnpj)
    if len(cx) != 14:
        return []
    visual = f"{cx[:2]}.{cx[2:5]}.{cx[5:8]}/{cx[8:12]}-{cx[12:]}"
    sanit = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in visual)
    sanit = " ".join(sanit.split())
    return [cx, visual, sanit, sanit.replace(" ", "_")]


def nome_pasta_cnpj_underlines(cnpj: str) -> str:
    """CNPJ para nome de pasta: 45_785_442_0003-90 (mesmo padrão da Autenticidade)."""
    xs = _nome_pasta_cnpj_sozinho(cnpj)
    return xs[-1] if xs else _cnpj_digitos_export(cnpj)


def _pasta_export_parece_empresa(
    nome: str, *, cnpj: str, codigo: str, emp_nome: str
) -> bool:
    """True se a pasta sob Autenticidade/Fechamento/Relatórios for desta empresa."""
    n = str(nome or "").strip()
    if not n:
        return False
    cx = _cnpj_digitos_export(cnpj)
    extraido = cnpj_de_nome_pasta(n)
    if extraido:
        return extraido == cx
    if n == emp_nome:
        return True
    for cand in _nome_pasta_cnpj_sozinho(cx):
        if n == cand:
            return True
    cod = str(codigo or "").strip()
    if cod:
        if n == cod:
            return True
        if (
            n.startswith(f"{cod} _")
            or n.startswith(f"{cod}_")
            or n.startswith(f"{cod} ")
            or n.startswith(f"{cod}—")
            or n.startswith(f"{cod} —")
        ):
            return True
    return False


def _pontuar_pasta_export(pasta: Path) -> tuple[int, float]:
    n = 0
    try:
        for f in pasta.rglob("*"):
            if f.is_file():
                n += 1
    except OSError:
        pass
    try:
        mt = float(pasta.stat().st_mtime)
    except OSError:
        mt = 0.0
    return (n, mt)


def _mesclar_pasta_export(origem: Path, destino: Path) -> None:
    """Move o conteúdo de origem → destino e remove a pasta origem."""
    if not origem.is_dir():
        return
    try:
        if _mesmo_caminho(origem, destino):
            return
    except OSError:
        if str(origem) == str(destino):
            return
    destino.mkdir(parents=True, exist_ok=True)
    try:
        itens = list(origem.iterdir())
    except OSError:
        return
    for item in itens:
        alvo = destino / item.name
        try:
            if item.is_dir():
                if alvo.is_dir():
                    _mesclar_pasta_export(item, alvo)
                elif alvo.exists():
                    alt = destino / f"{item.name}_mesclado"
                    k = 1
                    while alt.exists():
                        alt = destino / f"{item.name}_mesclado{k}"
                        k += 1
                    shutil.move(str(item), str(alt))
                else:
                    shutil.move(str(item), str(alvo))
            else:
                if not alvo.exists():
                    shutil.move(str(item), str(alvo))
                else:
                    try:
                        if alvo.stat().st_size == item.stat().st_size:
                            item.unlink(missing_ok=True)
                        else:
                            alt = destino / f"{item.stem}_dup{item.suffix}"
                            k = 1
                            while alt.exists():
                                alt = destino / f"{item.stem}_dup{k}{item.suffix}"
                                k += 1
                            shutil.move(str(item), str(alt))
                    except OSError:
                        pass
        except OSError:
            continue
    try:
        origem.rmdir()
    except OSError:
        shutil.rmtree(origem, ignore_errors=True)


def _listar_aliases_pasta_empresa_export(
    raiz: Path, g, cnpj: str, *, emp_nome: str | None = None
) -> list[Path]:
    if not raiz.is_dir():
        return []
    cx = _cnpj_digitos_export(cnpj)
    if len(cx) != 14:
        return []
    nome = emp_nome or _rotulo_empresa_pasta(g, cnpj)
    codigo = _codigo_catalogo_export(g, cnpj)
    out: list[Path] = []
    try:
        for item in raiz.iterdir():
            if not item.is_dir():
                continue
            if _pasta_export_parece_empresa(
                item.name, cnpj=cx, codigo=codigo, emp_nome=nome
            ):
                out.append(item)
    except OSError:
        return []
    return out


def garantir_pasta_empresa_export(
    raiz: Path | str,
    g,
    cnpj: str,
    *,
    criar_se_falta: bool = True,
    nome_pasta: str | None = None,
) -> Path:
    """
    Pasta canónica da empresa sob Autenticidade/Contabilidade.
    Se o cadastro mudou o nome e ficou pasta antiga (ex.: «578 _ Affonso» e
    «578 _ AFFONSO DESCARTÁVEIS…»), mescla tudo na pasta com o nome actual
    — não cria duplicados.

    criar_se_falta=False: só mescla o que já existe (não cria pasta vazia).
    """
    root = caminho_letra_rede(Path(raiz).expanduser())
    if criar_se_falta:
        root.mkdir(parents=True, exist_ok=True)
    elif not root.is_dir():
        return root / (str(nome_pasta or "").strip() or _rotulo_empresa_pasta(g, cnpj))

    emp_nome = str(nome_pasta or "").strip() or _rotulo_empresa_pasta(g, cnpj)
    destino = root / emp_nome
    aliases = _listar_aliases_pasta_empresa_export(
        root, g, cnpj, emp_nome=emp_nome
    )
    existentes = [p for p in aliases if p.is_dir()]
    if destino.is_dir() and destino not in existentes:
        existentes.append(destino)

    if not existentes:
        if criar_se_falta:
            destino.mkdir(parents=True, exist_ok=True)
        return destino

    existentes.sort(key=_pontuar_pasta_export, reverse=True)
    principal = destino if destino.is_dir() else existentes[0]

    if not destino.is_dir() and principal != destino:
        try:
            if not destino.exists():
                principal.rename(destino)
                principal = destino
            else:
                _mesclar_pasta_export(principal, destino)
                principal = destino
        except OSError:
            _mesclar_pasta_export(principal, destino)
            principal = destino

    if not destino.is_dir():
        if criar_se_falta:
            destino.mkdir(parents=True, exist_ok=True)
        else:
            return principal if principal.is_dir() else destino

    for outra in list(existentes):
        try:
            if not outra.is_dir():
                continue
            if _mesmo_caminho(outra, destino):
                continue
        except OSError:
            if str(outra) == str(destino):
                continue
        _mesclar_pasta_export(outra, destino)

    return destino


def consolidar_pastas_exportacao_empresa(g, cnpj: str) -> list[str]:
    """
    Mescla pastas duplicadas desta empresa em Autenticidade e Contabilidade.
    Não cria pastas novas vazias — só une o que já existe no disco.
    """
    cfg = carregar_config()
    afectados: list[str] = []
    for key, default_key in (
        ("pasta_autenticidade", "pasta_autenticidade"),
        ("pasta_contabilidade", "pasta_contabilidade"),
        ("pasta_excel", "pasta_excel"),
    ):
        raiz = cfg.get(key) or _defaults_pastas_instalacao().get(default_key)
        if not raiz:
            continue
        try:
            root = Path(raiz)
            if not root.is_dir():
                continue
            emp_nome = _rotulo_empresa_pasta(g, cnpj)
            aliases = _listar_aliases_pasta_empresa_export(
                root, g, cnpj, emp_nome=emp_nome
            )
            if not aliases:
                continue
            p = garantir_pasta_empresa_export(
                raiz,
                g,
                cnpj,
                criar_se_falta=False,
                nome_pasta=emp_nome,
            )
            if p.is_dir():
                afectados.append(str(p))
        except OSError:
            continue
    return afectados


def _nome_mes_arquivo_fiscal(g, mes: int) -> str:
    try:
        return g._arquivo_fiscal_nome_pasta_mes(int(mes))
    except Exception:
        return _MESES_NOME.get(int(mes), f"{int(mes):02d}")


def _lado_autenticidade_pasta_arquivo(nome_dir: str) -> str | None:
    """EP_ → emissão própria; T_/DOM_ → terceiros (igual ao arquivo XML/AAAA/Mês/)."""
    n = str(nome_dir or "")
    if n.startswith("EP_"):
        return "01_Emissao_propria"
    if n.startswith(("T_", "DOM_")):
        return "02_Terceiros"
    return None


def _chave_path_autenticidade(p: Path) -> str:
    try:
        return str(p.resolve()).lower()
    except OSError:
        return str(p).lower()


def _fila_autenticidade_indice(g, cnpj: str, ano: int, mes: int) -> list[tuple[str, Path]]:
    status_ok = {
        "NORMAIS",
        "AUTORIZADAS",
        "AUTORIZADA",
        "CANCELADOS",
        "DENEGADOS",
        "REJEITADOS",
    }
    fila: list[tuple[str, Path]] = []
    for it in _itens_mes(g, cnpj, ano, mes):
        st = str(it.get("status") or "").upper()
        if st and st not in status_ok:
            continue
        p = _path_item(g, cnpj, it)
        if p is None:
            continue
        lado = "01_Emissao_propria" if int(it.get("is_propria") or 0) == 1 else "02_Terceiros"
        fila.append((lado, p))
    return fila


def _fila_autenticidade_de_pastas_mes(
    g, cnpj: str, ano: int, mes: int
) -> list[tuple[str, Path]]:
    """Mesma origem do fallback da Contabilidade: XML/AAAA/Mês/EP_|T_|DOM_/."""
    nome_mes = _nome_mes_arquivo_fiscal(g, mes)
    src_mes = _src_mes_contabilidade(g, cnpj, ano, mes, nome_mes)
    if not src_mes.is_dir():
        return []
    fila: list[tuple[str, Path]] = []
    try:
        subdirs = sorted(src_mes.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []
    for pasta_ep in subdirs:
        if not pasta_ep.is_dir():
            continue
        lado = _lado_autenticidade_pasta_arquivo(pasta_ep.name)
        if not lado:
            continue
        try:
            xmls = sorted(pasta_ep.glob("*.xml"), key=lambda p: p.name.lower())
        except OSError:
            continue
        for xml in xmls:
            if xml.is_file():
                fila.append((lado, xml))
    return fila


def _fila_autenticidade(g, cnpj: str, ano: int, mes: int) -> list[tuple[str, Path]]:
    """Índice do mês + varredura das pastas EP_/T_ (como Fechamento no disco)."""
    por_path: dict[str, tuple[str, Path]] = {}
    for lado, p in _fila_autenticidade_indice(g, cnpj, ano, mes):
        por_path[_chave_path_autenticidade(p)] = (lado, p)
    for lado, p in _fila_autenticidade_de_pastas_mes(g, cnpj, ano, mes):
        ch = _chave_path_autenticidade(p)
        if ch not in por_path:
            por_path[ch] = (lado, p)
    return list(por_path.values())


def _mapa_esperado_autenticidade(
    fila: list[tuple[str, Path]], limite: int
) -> dict[str, int]:
    """relativo (posix) → tamanho esperado."""
    esperado: dict[str, int] = {}
    for lado in ("01_Emissao_propria", "02_Terceiros"):
        grupo = [p for ld, p in fila if ld == lado]
        if not grupo:
            continue
        for i in range(0, len(grupo), limite):
            parte = grupo[i : i + limite]
            lote_i = (i // limite) + 1
            if len(grupo) <= limite:
                prefixo = lado
            else:
                prefixo = f"{lado}/lote_{lote_i:02d}_ate_{limite}"
            for src in parte:
                try:
                    esperado[f"{prefixo}/{src.name}"] = src.stat().st_size
                except OSError:
                    continue
    return esperado


def _autenticidade_mapa_atuais(base: Path) -> dict[str, int]:
    """Tamanhos dos XML já copiados (dois níveis: lado / lote). Evita rglob."""
    atuais: dict[str, int] = {}
    try:
        with os.scandir(base) as it_lado:
            for lado in it_lado:
                if not lado.is_dir():
                    continue
                try:
                    with os.scandir(lado.path) as it2:
                        for ent in it2:
                            nome = ent.name
                            if ent.is_file() and nome.lower().endswith(".xml"):
                                atuais[f"{lado.name}/{nome}"] = ent.stat().st_size
                            elif ent.is_dir():
                                try:
                                    with os.scandir(ent.path) as it3:
                                        for xml in it3:
                                            if xml.is_file() and xml.name.lower().endswith(
                                                ".xml"
                                            ):
                                                atuais[
                                                    f"{lado.name}/{nome}/{xml.name}"
                                                ] = xml.stat().st_size
                                except OSError:
                                    continue
                except OSError:
                    continue
    except OSError:
        return {}
    return atuais


def _autenticidade_esta_atualizada(
    base: Path, fila: list[tuple[str, Path]], limite: int
) -> bool:
    if not base.is_dir() or not fila:
        return False
    esperado = _mapa_esperado_autenticidade(fila, limite)
    if not esperado:
        return False
    atuais = _autenticidade_mapa_atuais(base)
    # Extra XML antigo não impede o verde: o que importa é ter tudo o que o
    # arquivo manda, com o mesmo tamanho.
    return all(atuais.get(rel) == tam for rel, tam in esperado.items())


def exportar_autenticidade(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    pasta_destino: str | Path | None = None,
    *,
    max_por_pasta: int | None = None,
    pular_se_atualizado: bool = False,
) -> tuple[int, str, bool]:
    """
    Só para a app de status Sefaz.
    Estrutura plana: 2 pastas (própria / terceiros), XML soltos, ≤10k por lote.
    NÃO usa pastinhas EP_/T_ da contabilidade.
    Devolve (n_xml, mensagem, fez_update).
    """
    cfg = carregar_config()
    limite = int(max_por_pasta or cfg.get("max_xml_autenticidade") or MAX_AUTENTICIDADE)
    if limite < 1:
        limite = MAX_AUTENTICIDADE

    raiz = _normalizar_path_export(
        pasta_destino
        or cfg.get("pasta_autenticidade")
        or _defaults_pastas_instalacao()["pasta_autenticidade"]
    )
    # Ex.: …\Autenticidade\578 Affonso\2026_07\ (renomeia/mescla pastas antigas)
    emp_dir = garantir_pasta_empresa_export(raiz, g, cnpj)
    ok_w, err_w = _verificar_pasta_gravavel(raiz)
    if not ok_w:
        return (
            0,
            "Autenticidade — não foi possível gravar na pasta configurada.\n"
            f"{err_w}\n\nDestino: {raiz}",
            False,
        )
    base = emp_dir / f"{int(ano):04d}_{int(mes):02d}"
    n_idx = len(_fila_autenticidade_indice(g, cnpj, ano, mes))
    n_disk = len(_fila_autenticidade_de_pastas_mes(g, cnpj, ano, mes))
    fila = _fila_autenticidade(g, cnpj, ano, mes)
    if not fila:
        nome_mes = _nome_mes_arquivo_fiscal(g, mes)
        src_mes = _src_mes_contabilidade(g, cnpj, ano, mes, nome_mes)
        if src_mes.is_dir():
            det = (
                f"Índice: {n_idx} XML resolvíveis · pastas do mês: {n_disk} no disco.\n"
                f"Arquivo: {src_mes}"
            )
        else:
            det = f"Pasta do mês no arquivo não encontrada: {src_mes}"
        return (
            0,
            f"Nenhum XML para Autenticidade em {int(mes):02d}/{int(ano)}.\n{det}",
            False,
        )

    if pular_se_atualizado and _autenticidade_esta_atualizada(base, fila, limite):
        return (
            len(fila),
            f"Autenticidade já atualizada ({len(fila)} XML) — a abrir pasta.",
            False,
        )

    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return 0, f"Pasta inválida: {ex}", False

    atuais = _autenticidade_mapa_atuais(base)
    n_ok = 0
    n_copiados = 0
    n_falha = 0
    primeiro_erro = ""
    for lado in ("01_Emissao_propria", "02_Terceiros"):
        grupo = sorted(
            [p for ld, p in fila if ld == lado],
            key=lambda p: p.name.lower(),
        )
        if not grupo:
            continue
        for i in range(0, len(grupo), limite):
            parte = grupo[i : i + limite]
            lote_i = (i // limite) + 1
            if len(grupo) <= limite:
                pasta_lote = base / lado
                prefixo = lado
            else:
                pasta_lote = base / lado / f"lote_{lote_i:02d}_ate_{limite}"
                prefixo = f"{lado}/lote_{lote_i:02d}_ate_{limite}"
            try:
                pasta_lote.mkdir(parents=True, exist_ok=True)
            except OSError as ex:
                n_falha += len(parte)
                if not primeiro_erro:
                    primeiro_erro = str(ex)
                continue
            for src in parte:
                alvo = pasta_lote / src.name
                rel = f"{prefixo}/{src.name}"
                try:
                    tam = src.stat().st_size
                except OSError as ex:
                    n_falha += 1
                    if not primeiro_erro:
                        primeiro_erro = f"{src.name}: {ex}"
                    continue
                if atuais.get(rel) == tam:
                    n_ok += 1
                    continue
                try:
                    shutil.copy2(src, alvo)
                    n_ok += 1
                    n_copiados += 1
                    atuais[rel] = tam
                except OSError as ex:
                    n_falha += 1
                    if not primeiro_erro:
                        primeiro_erro = f"{src.name} → {alvo}: {ex}"

    cfg["pasta_autenticidade"] = str(raiz)
    guardar_config(cfg)

    if n_ok == 0:
        msg = (
            f"Não foi possível copiar XML para Autenticidade ({int(mes):02d}/{int(ano)}).\n"
            f"Destino: {base}\n"
            f"Na fila: {len(fila)} · falhas: {n_falha}."
        )
        if primeiro_erro:
            msg += f"\nPrimeiro erro: {primeiro_erro}"
        return 0, msg, False

    origem_nota = ""
    if n_disk > n_idx:
        origem_nota = f"\n(Índice: {n_idx} · disco EP_/T_: {n_disk} · fila única: {len(fila)}.)"

    msg = (
        f"AUTENTICIDADE — pastas ABERTAS (sem ZIP): {n_ok} XML em\n{base}\n"
        f"01_Emissao_propria / 02_Terceiros · máx. {limite} XML por pasta/lote."
    )
    if n_copiados:
        msg += f"\n{n_copiados} copiado(s) agora."
    if n_falha:
        msg += f"\n{n_falha} falha(s) de cópia."
        if primeiro_erro:
            msg += f" Ex.: {primeiro_erro}"
    msg += origem_nota
    return n_ok, msg, bool(n_copiados)


def exportar_contabilidade_pastas(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    pasta_destino: str | Path | None = None,
) -> tuple[int, str]:
    """Compat: devolve (n_xml, msg). Preferir exportar_contabilidade_zip."""
    path, n, msg, _ = exportar_contabilidade_zip(
        g, cnpj, ano, mes, destino=pasta_destino
    )
    return (n if path else 0), msg


_MESES_NOME = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


def _src_mes_contabilidade(g, cnpj: str, ano: int, mes: int, nome_mes: str) -> Path:
    emp = g._arquivo_fiscal_pasta_empresa(cnpj)
    return emp / "XML" / f"{int(ano):04d}" / nome_mes


def _zip_pertence_a_pasta_ep(nome_zip: str, nome_pasta: str) -> bool:
    """EP_….zip ou EP_…_pt02.zip (lote) correspondem à pasta EP_… do arquivo."""
    n = str(nome_zip or "")
    base = str(nome_pasta or "")
    if not n.lower().endswith(".zip") or not base:
        return False
    stem = n[:-4]
    if stem == base:
        return True
    return bool(re.fullmatch(re.escape(base) + r"_pt\d+", stem))


def _contabilidade_esta_atualizada(src_mes: Path, pasta_mes: Path) -> tuple[bool, int]:
    """Compara ZIPs do destino com pastas EP_/T_ do arquivo (nome + tamanho)."""
    if not src_mes.is_dir() or not pasta_mes.is_dir():
        return False, 0
    total = 0
    cobertos: set[str] = set()
    try:
        zips_mes = [p for p in pasta_mes.glob("*.zip") if p.is_file()]
        for pasta_ep in sorted(src_mes.iterdir()):
            if not pasta_ep.is_dir():
                continue
            xmls = sorted(pasta_ep.glob("*.xml"))
            if not xmls:
                continue
            mapa_src = {x.name: x.stat().st_size for x in xmls}
            total += len(mapa_src)
            zips = [p for p in zips_mes if _zip_pertence_a_pasta_ep(p.name, pasta_ep.name)]
            if not zips:
                return False, 0
            cobertos.update(p.name for p in zips)
            mapa_zip: dict[str, int] = {}
            for zp in zips:
                with zipfile.ZipFile(zp, "r") as zf:
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        mapa_zip[Path(info.filename).name] = info.file_size
            if mapa_zip != mapa_src:
                return False, 0
        # ZIP a mais (lote antigo) não impede: cada pasta EP_/T_ já está coberta.
    except (OSError, zipfile.BadZipFile):
        return False, 0
    return total > 0, total


def exportar_contabilidade_zip(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    *,
    destino: str | Path | None = None,
    pular_se_atualizado: bool = False,
) -> tuple[str | None, int, str, bool]:
    """
    Contabilidade / Domínio: um .zip por pastinha EP_/T_ (separadinho).
    Cada ZIP tem os XML na raiz — sem pastas abertas, sem ZIP dentro de ZIP.
    destino = pasta escolhida no pop-up; grava Empresa\\AAAA\\Mês\\EP_….zip
    Devolve (pasta_do_mês|None, n_xml, mensagem, fez_update).
    """
    cfg = carregar_config()
    try:
        nome_mes = g._arquivo_fiscal_nome_pasta_mes(int(mes))
    except Exception:
        nome_mes = _MESES_NOME.get(int(mes), f"{int(mes):02d}")

    if destino is None:
        raiz = Path(
            cfg.get("pasta_contabilidade")
            or _defaults_pastas_instalacao()["pasta_contabilidade"]
        ).expanduser()
    else:
        dest = Path(destino).expanduser()
        raiz = dest.parent if dest.suffix.lower() == ".zip" else dest

    try:
        raiz = caminho_letra_rede(raiz)
        raiz.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return None, 0, str(ex), False

    dest_emp = garantir_pasta_empresa_export(raiz, g, cnpj)
    pasta_mes = dest_emp / f"{int(ano):04d}" / nome_mes
    src_mes = _src_mes_contabilidade(g, cnpj, ano, mes, nome_mes)

    if pular_se_atualizado:
        ok_sync, n_sync = _contabilidade_esta_atualizada(src_mes, pasta_mes)
        if ok_sync:
            return (
                str(pasta_mes),
                n_sync,
                f"Contabilidade já atualizada ({n_sync} XML) — a abrir pasta.",
                False,
            )

    n_aus = 0
    total_xml = 0
    zips: list[Path] = []

    # Contabilidade: dest_emp/AAAA/Mês/EP_….zip com XML na raiz de cada ZIP
    if hasattr(g, "_arquivo_fiscal_zip_espelho_filtrado"):
        partes, total_xml, n_aus, err = g._arquivo_fiscal_zip_espelho_filtrado(
            cnpj,
            str(dest_emp),
            ano=int(ano),
            mes=int(mes),
            espelho_pasta_zip=False,
        )
        if err and not partes:
            return None, 0, err, False
        zips = [Path(p) for p in (partes or []) if Path(p).is_file()]
        if not zips and pasta_mes.is_dir():
            zips = sorted(pasta_mes.glob("*.zip"))

    if not zips:
        if not src_mes.is_dir():
            return None, 0, f"Pasta do mês não encontrada no arquivo: {src_mes}", False
        try:
            pasta_mes.mkdir(parents=True, exist_ok=True)
            for antigo in pasta_mes.glob("*.zip"):
                try:
                    antigo.unlink()
                except OSError:
                    pass
            total_xml = 0
            for pasta_ep in sorted(src_mes.iterdir()):
                if not pasta_ep.is_dir():
                    continue
                xmls = sorted(pasta_ep.glob("*.xml"))
                if not xmls:
                    continue
                zip_path = pasta_mes / f"{pasta_ep.name}.zip"
                with zipfile.ZipFile(
                    zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
                ) as zf:
                    for xml in xmls:
                        zf.write(xml, arcname=xml.name)
                        total_xml += 1
                zips.append(zip_path)
        except OSError as ex:
            return None, 0, f"Erro ao gravar ZIPs: {ex}", False

    if not zips:
        return None, 0, "Não foi possível criar os ZIPs da Contabilidade.", False

    cfg["pasta_contabilidade"] = str(raiz)
    guardar_config(cfg)
    pasta_txt = str(pasta_mes if pasta_mes.is_dir() else zips[0].parent)
    msg = (
        f"CONTABILIDADE — {len(zips)} ZIP(s) separados (um por EP_/T_), {total_xml} XML:\n"
        f"{pasta_txt}\n"
        "Cada ZIP tem XML na raiz — Domínio importa um de cada vez."
    )
    if n_aus:
        msg += f"\n({n_aus} ausente(s) no disco.)"
    return pasta_txt, total_xml, msg, True


def sieg_mes_esta_atualizado(g, cnpj: str, ano: int, mes: int) -> bool:
    dest_s = str(carregar_config().get("pasta_sieg_importar") or "").strip()
    if not dest_s:
        return False
    dest = Path(dest_s)
    paths: list[Path] = []
    for it in _itens_mes(g, cnpj, ano, mes):
        p = _path_item(g, cnpj, it)
        if p is not None:
            paths.append(p)
    return _sieg_esta_atualizado(paths, dest)


def autenticidade_mes_esta_atualizada(g, cnpj: str, ano: int, mes: int) -> bool:
    cfg = carregar_config()
    limite = int(cfg.get("max_xml_autenticidade") or MAX_AUTENTICIDADE)
    if limite < 1:
        limite = MAX_AUTENTICIDADE
    raiz = _normalizar_path_export(
        cfg.get("pasta_autenticidade")
        or _defaults_pastas_instalacao()["pasta_autenticidade"]
    )
    emp_dir = garantir_pasta_empresa_export(raiz, g, cnpj, criar_se_falta=False)
    if not emp_dir.is_dir():
        return False
    base = emp_dir / f"{int(ano):04d}_{int(mes):02d}"
    fila = _fila_autenticidade(g, cnpj, ano, mes)
    return _autenticidade_esta_atualizada(base, fila, limite)


def contabilidade_mes_esta_atualizada(g, cnpj: str, ano: int, mes: int) -> bool:
    cfg = carregar_config()
    raiz = Path(
        cfg.get("pasta_contabilidade")
        or _defaults_pastas_instalacao()["pasta_contabilidade"]
    ).expanduser()
    dest_emp = garantir_pasta_empresa_export(raiz, g, cnpj, criar_se_falta=False)
    if not dest_emp.is_dir():
        return False
    try:
        nome_mes = g._arquivo_fiscal_nome_pasta_mes(int(mes))
    except Exception:
        nome_mes = _MESES_NOME.get(int(mes), f"{int(mes):02d}")
    pasta_mes = dest_emp / f"{int(ano):04d}" / nome_mes
    src_mes = _src_mes_contabilidade(g, cnpj, ano, mes, nome_mes)
    ok, _n = _contabilidade_esta_atualizada(src_mes, pasta_mes)
    return bool(ok)


def exportar_zip_unico_filtrado(
    g,
    cnpj: str,
    pasta_destino: str | Path,
    *,
    ano: int | None = None,
    mes: int | None = None,
    meses: list[int] | None = None,
    is_propria: int | None = None,
    status: str | None = None,
    serie: str | None = None,
    modelo: str | None = None,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    numero_inicio: int | None = None,
    numero_fim: int | None = None,
    uf_parceiro: str | list | None = None,
    cfop: str | list | None = None,
) -> tuple[str | None, str]:
    """
    Um único .zip com TODOS os XML do filtro na raiz do ZIP — sem pastinhas EP_/T_.
    """
    itens = g._arquivo_fiscal_listar(
        cnpj,
        ano=ano,
        mes=mes,
        meses=meses,
        is_propria=is_propria,
        status=status,
        serie=serie,
        modelo=modelo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        numero_inicio=numero_inicio,
        numero_fim=numero_fim,
        uf_parceiro=uf_parceiro,
        cfop=cfop,
    ) or []
    if not itens:
        return None, "Nenhum XML no arquivo com estes filtros."

    dest = _normalizar_path_export(pasta_destino)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return None, f"Pasta de destino inválida: {ex}"

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    partes_nome = ["filtro"]
    if ano is not None:
        partes_nome.append(str(int(ano)))
    if mes is not None:
        partes_nome.append(f"{int(mes):02d}")
    elif meses:
        partes_nome.append("-".join(f"{int(m):02d}" for m in sorted(meses)))
    if status:
        partes_nome.append(str(status).lower()[:12])
    if serie:
        partes_nome.append(f"ser{serie}")
    ufs = []
    if hasattr(g, "_arquivo_fiscal_ufs_filtro"):
        try:
            ufs = g._arquivo_fiscal_ufs_filtro(uf_parceiro) or []
        except Exception:
            ufs = []
    if ufs:
        partes_nome.append("uf" + "-".join(ufs[:4]))
    cfops_nome = []
    if hasattr(g, "_arquivo_fiscal_cfops_filtro"):
        try:
            cfops_nome = g._arquivo_fiscal_cfops_filtro(cfop) or []
        except Exception:
            cfops_nome = []
    if cfops_nome:
        partes_nome.append("cfop" + "-".join(cfops_nome[:4]))
    zip_path = dest / ("_".join(partes_nome) + f"_{stamp}.zip")

    n_ok = 0
    n_aus = 0
    nomes_no_zip: set[str] = set()
    try:
        with zipfile.ZipFile(
            zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            for it in itens:
                p = _path_item(g, cnpj, it)
                if p is None:
                    n_aus += 1
                    continue
                nome = p.name
                if nome in nomes_no_zip:
                    chave = "".join(c for c in str(it.get("chave") or "") if c.isdigit())[:44]
                    stem = Path(nome).stem
                    nome = f"{stem}_{chave[-8:] or n_ok}.xml" if chave else f"{stem}_{n_ok}.xml"
                nomes_no_zip.add(nome)
                try:
                    zf.write(p, arcname=nome)
                    n_ok += 1
                except OSError:
                    n_aus += 1
    except OSError as ex:
        return None, f"Erro ao gravar ZIP: {ex}"

    if n_ok == 0:
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None, "Nenhum XML encontrado no disco para estes filtros."

    msg = f"{n_ok} XML num único ZIP (sem pastas internas):\n{zip_path}"
    if n_aus:
        msg += f"\n({n_aus} ausente(s) no disco — não entraram.)"
    return str(zip_path), msg


def normalizar_lista_chaves44(texto: str) -> list[str]:
    """Extrai chaves de 44 dígitos de texto colado (linhas, vírgulas, espaços)."""
    import re

    out: list[str] = []
    vistos: set[str] = set()
    for m in re.finditer(r"\d{44}", str(texto or "")):
        k = m.group(0)
        if k not in vistos:
            vistos.add(k)
            out.append(k)
    return out


def exportar_zip_chaves(
    g,
    cnpj: str,
    chaves: list[str],
    destino: str | Path,
) -> tuple[str | None, str]:
    """ZIP na raiz com os XML das chaves seleccionadas no painel (igual à Online)."""
    bases: list[str] = []
    vistos: set[str] = set()
    for ch in chaves or []:
        k = "".join(c for c in str(ch or "") if c.isdigit())[:44]
        if len(k) == 44 and k not in vistos:
            vistos.add(k)
            bases.append(k)
    if not bases:
        return None, "Não há XML nestas linhas. Em buracos use Extrair para Excel. Em canceladas/denegadas/rejeitadas/autorizadas, seleccione as notas."
    loc = getattr(g, "_arquivo_fiscal_localizar_por_chaves", None)
    if not callable(loc):
        return None, "Motor sem localização por chave — reinicie o Garimpeiro."
    mapa = loc(cnpj, bases, incluir_eventos=True) or {}
    dest = _exigir_fora_do_c(Path(destino))
    dest.parent.mkdir(parents=True, exist_ok=True)
    n_ok = 0
    n_aus = 0
    nomes: set[str] = set()
    try:
        with zipfile.ZipFile(
            dest, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zf:
            for base in bases:
                info = mapa.get(base) or {}
                itens = list(info.get("itens") or [])
                escolhido = None
                auts = [i for i in itens if i.get("existe_disco") and not i.get("evento")]
                if auts:
                    escolhido = auts[0]
                else:
                    evs = [i for i in itens if i.get("existe_disco")]
                    escolhido = evs[0] if evs else None
                if not escolhido:
                    n_aus += 1
                    continue
                p = Path(str(escolhido.get("caminho_absoluto") or ""))
                if not p.is_file():
                    n_aus += 1
                    continue
                nome = p.name
                if nome in nomes:
                    nome = f"{p.stem}_{base[-8:]}.xml"
                nomes.add(nome)
                try:
                    zf.write(p, arcname=nome)
                    n_ok += 1
                except OSError:
                    n_aus += 1
    except OSError as ex:
        return None, f"Erro ao gravar ZIP: {ex}"
    if n_ok == 0:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        return None, "Nenhum XML encontrado no disco para estas chaves."
    msg = f"{n_ok} XML num único ZIP:\n{dest}"
    if n_aus:
        msg += f"\n({n_aus} ausente(s) no disco — não entraram.)"
    return str(dest), msg


def caminhos_novos_do_lote(
    g,
    cnpj: str,
    chaves_novas: list[str],
) -> list[Path]:
    """Resolve caminhos no disco das chaves gravadas como novas neste garimpo."""
    return [it["path"] for it in itens_novos_do_lote(g, cnpj, chaves_novas)]


def itens_novos_do_lote(
    g,
    cnpj: str,
    chaves_novas: list[str],
) -> list[dict]:
    """Metadados + caminho no disco das chaves novas deste garimpo."""
    out: list[dict] = []
    if not chaves_novas:
        return out
    emp = g._arquivo_fiscal_pasta_empresa(cnpj)
    conn = g._arquivo_fiscal_conn(cnpj)
    try:
        for i in range(0, len(chaves_novas), 400):
            bloco = chaves_novas[i : i + 400]
            ph = ",".join("?" * len(bloco))
            rows = conn.execute(
                f"""
                SELECT caminho_relativo, is_propria, ano, mes, status
                FROM xml_arquivo WHERE chave IN ({ph})
                """,
                bloco,
            ).fetchall()
            for rel, is_prop, ano, mes, status in rows:
                p = emp / str(rel or "").replace("/", "\\")
                if not p.is_file():
                    continue
                out.append(
                    {
                        "path": p,
                        "is_propria": int(is_prop or 0),
                        "ano": int(ano or 0),
                        "mes": int(mes or 0),
                        "status": str(status or "").upper(),
                    }
                )
    finally:
        conn.close()
    return out


def _pasta_lote_autenticidade(base_lado: Path, limite: int) -> Path:
    """Escolhe pasta com espaço (< limite XML), criando lote novo se preciso."""
    base_lado.mkdir(parents=True, exist_ok=True)
    lotes = sorted(
        p
        for p in base_lado.iterdir()
        if p.is_dir() and p.name.lower().startswith("lote_")
    )
    if lotes:
        for lote in lotes:
            if sum(1 for _ in lote.glob("*.xml")) < limite:
                return lote
        n = len(lotes) + 1
        novo = base_lado / f"lote_{n:02d}_ate_{limite}"
        novo.mkdir(parents=True, exist_ok=True)
        return novo

    n_diretos = sum(1 for _ in base_lado.glob("*.xml"))
    if n_diretos < limite:
        return base_lado

    # Pasta plana já cheia → overflow em lote_02 (lote_01 = os XML que já estão na raiz)
    n = 2
    while True:
        novo = base_lado / f"lote_{n:02d}_ate_{limite}"
        if not novo.exists() or sum(1 for _ in novo.glob("*.xml")) < limite:
            novo.mkdir(parents=True, exist_ok=True)
            return novo
        n += 1


def enviar_xmls_novos_para_autenticidade(
    g,
    cnpj: str,
    chaves_novas: list[str],
    *,
    destino: str | Path | None = None,
) -> tuple[int, int, str]:
    """
    Copia só os XML novos do garimpo para Autenticidade (append, sem apagar o mês).
    Estrutura: Empresa\\AAAA_MM\\01_Emissao_propria|02_Terceiros\\ (lotes ≤10k).
    """
    cfg = carregar_config()
    limite = int(cfg.get("max_xml_autenticidade") or MAX_AUTENTICIDADE)
    if limite < 1:
        limite = MAX_AUTENTICIDADE
    raiz = Path(
        destino
        or cfg.get("pasta_autenticidade")
        or _defaults_pastas_instalacao()["pasta_autenticidade"]
    ).expanduser()
    try:
        raiz = caminho_letra_rede(raiz)
        raiz.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return 0, 0, f"Não foi possível criar/abrir {raiz}: {ex}"

    status_ok = {"NORMAIS", "CANCELADOS", "DENEGADOS", "REJEITADOS", ""}
    emp_dir = garantir_pasta_empresa_export(raiz, g, cnpj)
    itens = itens_novos_do_lote(g, cnpj, chaves_novas)
    ok = 0
    skip = 0
    destinos: set[str] = set()

    for it in itens:
        st = it.get("status") or ""
        if st and st not in status_ok:
            skip += 1
            continue
        ano, mes = int(it.get("ano") or 0), int(it.get("mes") or 0)
        if ano <= 0 or not (1 <= mes <= 12):
            skip += 1
            continue
        src = Path(it["path"])
        lado = (
            "01_Emissao_propria"
            if int(it.get("is_propria") or 0) == 1
            else "02_Terceiros"
        )
        base = emp_dir / f"{ano:04d}_{mes:02d}" / lado
        try:
            pasta_lote = _pasta_lote_autenticidade(base, limite)
            alvo = pasta_lote / src.name
            if alvo.is_file() and alvo.stat().st_size == src.stat().st_size:
                skip += 1
                continue
            shutil.copy2(src, alvo)
            ok += 1
            destinos.add(str(base.parent))
        except OSError:
            skip += 1

    cfg["pasta_autenticidade"] = str(raiz)
    guardar_config(cfg)
    if destinos:
        dest_txt = "; ".join(sorted(destinos)[:3])
        if len(destinos) > 3:
            dest_txt += f" (+{len(destinos) - 3})"
    else:
        dest_txt = str(raiz)
    return ok, skip, dest_txt


# Leiaute Domínio — igual a ProntoParaImportar.txt (Cancelar e Inutilizar)
_DOMINIO_INUT_N_CAMPOS = 40
_DOMINIO_INUT_REGISTRO = "2000"
_DOMINIO_INUT_ESPECIE = "36"
_DOMINIO_INUT_CNPJ = "111.111.111-11"
_DOMINIO_INUT_ACUM = "98"
_DOMINIO_INUT_CFOP = "5949"
_DOMINIO_INUT_SITUACAO = "C"
_DOMINIO_INUT_CAMPO36 = "8"
_DOMINIO_INUT_NOME_FICHEIRO = "Inutilizadas Dominio.txt"
_DOMINIO_CANCEL_CFOP_TESTE_NOME = "Canceladas Dominio CFOP-teste.txt"


def _cfop_principal_csv(cfops_db: str) -> str:
    s = str(cfops_db or "").strip()
    if not s or s == ",":
        return ""
    for part in s.split(","):
        c = "".join(ch for ch in part if ch.isdigit())[:4]
        if len(c) == 4:
            return c
    return ""


def cfop_dominio_escrituracao_de_nf(
    cfop_origem: str,
    *,
    tp_nf: str = "",
    operacao: str = "",
    cfop_fallback: str = "5949",
) -> str:
    """
    CFOP Domínio para cancelamento/inutilização conforme CFOP da NF:
    1xxx→1949, 2xxx→2949, 5xxx→5949, 6xxx→6949.
    Sem CFOP na nota: entrada (tpNF=0) → 1949/2949; saída → 5949/6949.
    """
    c = "".join(ch for ch in str(cfop_origem or "") if ch.isdigit())[:4]
    mapa = {"1": "1949", "2": "2949", "5": "5949", "6": "6949"}
    if len(c) == 4 and c[0] in mapa:
        return mapa[c[0]]
    tp = str(tp_nf or "").strip()
    op = str(operacao or "").upper()
    entrada = tp == "0" or "ENTRADA" in op
    saida = tp == "1" or "SAIDA" in op or "SAÍDA" in op
    if entrada:
        return "2949" if c.startswith("2") else "1949"
    if saida:
        return "6949" if c.startswith("6") else "5949"
    fb = "".join(ch for ch in str(cfop_fallback or "") if ch.isdigit())[:4]
    return fb if len(fb) == 4 else "5949"


def _carregar_canceladas_mes_dominio_cfop(g, cnpj: str, ano: int, mes: int) -> list[dict]:
    """Canceladas do mês com cfops/data para TXT Domínio (teste CFOP por NF)."""
    itens = _carregar_canceladas_mes_sepultador(g, cnpj, int(ano), int(mes))
    conn_fn = getattr(g, "_arquivo_fiscal_conn", None)
    if not callable(conn_fn):
        return itens
    extra: dict[str, dict] = {}
    try:
        conn = conn_fn(cnpj)
        try:
            rows = conn.execute(
                """
                SELECT chave, cfops, data_emissao, operacao
                FROM xml_arquivo
                WHERE cnpj_empresa = ?
                  AND ano = ? AND mes = ?
                  AND upper(trim(COALESCE(status, ''))) = 'CANCELADOS'
                """,
                (cnpj, int(ano), int(mes)),
            ).fetchall()
            for chave, cfops, data_em, operacao in rows:
                k = "".join(c for c in str(chave or "") if c.isdigit())[:44]
                if len(k) == 44:
                    extra[k] = {
                        "cfops": cfops,
                        "data_emissao": data_em,
                        "operacao": operacao,
                    }
        finally:
            conn.close()
    except Exception:
        return itens
    out: list[dict] = []
    for it in itens:
        row = dict(it)
        k = "".join(
            c for c in str(row.get("chave") or row.get("Chave") or "") if c.isdigit()
        )[:44]
        if k in extra:
            row.update(extra[k])
        out.append(row)
    return out


def exportar_txt_canceladas_dominio_cfop_inteligente_teste(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    *,
    acumulador: str | None = None,
    cfop_fallback: str | None = None,
    destino: str | Path | None = None,
) -> tuple[str | None, int, str]:
    """
    TESTE — gera **dois** ficheiros no mesmo sítio:
    1) SPED Sepultador (igual ao «Gerar .txt Domínio» nas canceladas);
    2) Escrituração Domínio reg.2000 com CFOP por NF (layout Inutilizadas Dominio.txt).
    """
    cx_emp = "".join(c for c in str(cnpj or "") if c.isdigit())[:14]
    if destino:
        dest_escr = _exigir_fora_do_c(Path(destino))
    else:
        emp = _rotulo_empresa_pasta(g, cnpj)
        dest_dir = _exigir_fora_do_c(
            pasta_dados_garimpeiro() / "DominioCanceladasTeste" / emp
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_escr = dest_dir / _DOMINIO_CANCEL_CFOP_TESTE_NOME

    dest_sped = dest_escr.parent / (
        f"SPED_CANCELADAS_{cx_emp}_{int(ano)}{int(mes):02d}_teste.txt"
    )
    path_sped, n_sped, msg_sped = exportar_txt_sepultador_canceladas(
        g, cnpj, int(ano), int(mes), destino=dest_sped
    )
    if not path_sped or n_sped <= 0:
        return None, 0, f"Sepultador SPED (igual ao botão principal):\n{msg_sped}"

    itens = _carregar_canceladas_mes_dominio_cfop(g, cnpj, int(ano), int(mes))
    raiz_emp: Path | None = None
    pasta_fn = getattr(g, "_arquivo_fiscal_pasta_empresa", None)
    if callable(pasta_fn):
        try:
            raiz_emp = Path(pasta_fn(cnpj))
        except Exception:
            raiz_emp = None

    cx_emp = "".join(c for c in str(cnpj or "") if c.isdigit())[:14]
    fb = str(cfop_fallback or _DOMINIO_INUT_CFOP).strip() or _DOMINIO_INUT_CFOP

    def _path_xml_nfe(k: str, lst: list[dict]) -> Path | None:
        if raiz_emp is None or not raiz_emp.is_dir():
            return None
        for it in lst:
            rel = str(it.get("caminho_relativo") or it.get("Caminho") or "")
            nome = str(it.get("nome_arquivo") or it.get("Arquivo") or "")
            rel_s = rel.replace("/", "\\")
            path = raiz_emp / rel_s if rel_s else None
            if path and path.is_file() and "cancelamento" not in path.name.lower():
                return path
            if nome:
                p2 = (path.parent / nome) if path else (raiz_emp / nome)
                if p2.is_file() and "cancelamento" not in p2.name.lower():
                    return p2
        return None

    por_chave: dict[str, list[dict]] = {}
    for it in itens:
        k = "".join(c for c in str(it.get("chave") or it.get("Chave") or "") if c.isdigit())[
            :44
        ]
        if len(k) != 44:
            continue
        por_chave.setdefault(k, []).append(it)

    linhas_dados: list[str] = []
    resumo_cfop: dict[str, int] = {}
    vistos: set[str] = set()

    for k, lst in por_chave.items():
        if k in vistos:
            continue
        if len(cx_emp) == 14:
            cx_ch = _cnpj_emitente_da_chave44(k)
            if cx_ch and cx_ch != cx_emp:
                continue
        vistos.add(k)
        it0 = lst[0]
        try:
            nota = int(it0.get("numero") or it0.get("Nota") or 0)
            ser = int(str(it0.get("serie") or it0.get("Série") or "0").strip() or "0")
        except (TypeError, ValueError):
            continue
        if nota <= 0:
            continue
        cfop_orig = _cfop_principal_csv(str(it0.get("cfops") or ""))
        tp_nf = ""
        operacao = str(it0.get("operacao") or "")
        path_xml = _path_xml_nfe(k, lst)
        if path_xml and path_xml.is_file():
            try:
                dx = _ler_xml_nfe_sepultador(path_xml)
                if not cfop_orig and dx.get("cfop"):
                    cfop_orig = str(dx.get("cfop") or "")
                if dx.get("tp_nf") in ("0", "1"):
                    tp_nf = str(dx["tp_nf"])
            except Exception:
                pass
        cfop_dom = cfop_dominio_escrituracao_de_nf(
            cfop_orig, tp_nf=tp_nf, operacao=operacao, cfop_fallback=fb
        )
        resumo_cfop[cfop_dom] = resumo_cfop.get(cfop_dom, 0) + 1
        origem = str(it0.get("data_emissao") or it0.get("Data Emissão") or "")
        data_txt = _data_txt_dominio_competencia(int(ano), int(mes), origem)
        linhas_dados.append(
            _linha_txt_inutilizada_dominio(
                ser,
                nota,
                data_txt,
                acumulador=str(acumulador or "").strip(),
                cfop=cfop_dom,
            )
        )

    if not linhas_dados:
        return (
            path_sped,
            n_sped,
            f"[TESTE] Só o Sepultador SPED foi gerado (igual «Gerar .txt Domínio»):\n"
            f"{path_sped}\n\n"
            f"Escrituração CFOP: nenhuma cancelada no mês {int(mes):02d}/{int(ano)}.",
        )

    try:
        dest_escr.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return (
            path_sped,
            n_sped,
            f"SPED gerado em {path_sped}\n\nErro ao gravar escrituração CFOP: {ex}",
        )

    cab = "|".join([""] * _DOMINIO_INUT_N_CAMPOS)
    texto = cab + "\n" + "\n".join(linhas_dados) + "\n"
    dest_escr.write_text(texto, encoding="latin-1")
    acum_u = str(acumulador or _DOMINIO_INUT_ACUM).strip() or _DOMINIO_INUT_ACUM
    partes = [f"{n}× CFOP {c}" for c, n in sorted(resumo_cfop.items())]
    msg = (
        f"[TESTE — 2 ficheiros]\n\n"
        f"1) Sepultador SPED (= «Gerar .txt Domínio» nas canceladas):\n   {path_sped}\n\n"
        f"2) Escrituração Domínio (reg. 2000, CFOP por NF — compare com "
        f"«Inutilizadas Dominio.txt»):\n   {dest_escr}\n"
        f"   Acumulador {acum_u} · {', '.join(partes)}\n\n"
        f"O SPED (ficheiro 1) não tem CFOP; o CFOP por nota está só no ficheiro 2."
    )
    return str(dest_escr), len(linhas_dados), msg


def _data_txt_dominio_competencia(ano: int, mes: int, data_origem: str | None) -> str:
    """Mantém o dia da Sefaz; força mês/ano da competência (não escriturar no mês seguinte)."""
    ult = calendar.monthrange(int(ano), int(mes))[1]
    dia = 1
    s = str(data_origem or "").strip()
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        dia = int(m.group(1))
    else:
        m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            dia = int(m.group(3))
    dia = max(1, min(int(dia), ult))
    return f"{dia:02d}/{int(mes):02d}/{int(ano):04d}"


def _linha_txt_inutilizada_dominio(
    serie: int,
    nota: int,
    data_txt: str,
    *,
    acumulador: str,
    cfop: str,
) -> str:
    campos = [""] * _DOMINIO_INUT_N_CAMPOS
    campos[0] = _DOMINIO_INUT_REGISTRO
    campos[1] = _DOMINIO_INUT_ESPECIE
    campos[2] = _DOMINIO_INUT_CNPJ
    campos[3] = str(acumulador or _DOMINIO_INUT_ACUM).strip() or _DOMINIO_INUT_ACUM
    campos[4] = str(cfop or _DOMINIO_INUT_CFOP).strip() or _DOMINIO_INUT_CFOP
    campos[8] = str(int(nota))
    campos[9] = str(int(serie))
    campos[10] = str(int(nota))
    campos[11] = data_txt
    campos[17] = _DOMINIO_INUT_SITUACAO
    campos[36] = _DOMINIO_INUT_CAMPO36
    return "|".join(campos)


def exportar_txt_inutilizadas_dominio(
    g,
    cnpj: str,
    ano: int,
    mes: int,
    *,
    acumulador: str | None = None,
    cfop: str | None = None,
    destino: str | Path | None = None,
) -> tuple[str | None, int, str]:
    """
    Gera Inutilizadas Dominio.txt (leiaute Domínio, registo 2000) das inutilizadas
    do mês. Data = dia original (Sefaz), mês/ano da competência seleccionada.
    """
    itens = []
    listar = getattr(g, "_arquivo_fiscal_listar_inutilizadas_competencia", None)
    if callable(listar):
        itens = listar(cnpj, int(ano), int(mes)) or []
    if not itens:
        tabelas = getattr(g, "_arquivo_fiscal_tabelas_status_periodo", None)
        if callable(tabelas):
            dfs = tabelas(cnpj, int(ano), [int(mes)], is_propria=1) or {}
            df = dfs.get("INUTILIZADOS")
            if df is not None and not getattr(df, "empty", True):
                for _, row in df.iterrows():
                    itens.append(
                        {
                            "serie": row.get("Série") or row.get("Serie"),
                            "numero": row.get("Nota"),
                            "data_emissao": row.get("Data Inutilização")
                            or row.get("Data Emissão"),
                        }
                    )
    linhas_dados: list[str] = []
    vistos: set[tuple[int, int]] = set()
    for it in itens:
        try:
            nota = int(it.get("numero") or it.get("Nota") or 0)
            ser = int(str(it.get("serie") or it.get("Série") or "0").strip() or "0")
        except (TypeError, ValueError):
            continue
        if nota <= 0:
            continue
        k = (ser, nota)
        if k in vistos:
            continue
        vistos.add(k)
        origem = (
            str(it.get("data_emissao") or "")
            or str(it.get("Data Inutilização") or "")
        )
        data_txt = _data_txt_dominio_competencia(int(ano), int(mes), origem)
        linhas_dados.append(
            _linha_txt_inutilizada_dominio(
                ser,
                nota,
                data_txt,
                acumulador=str(acumulador or "").strip(),
                cfop=str(cfop or "").strip(),
            )
        )
    if not linhas_dados:
        return None, 0, f"Nenhuma inutilizada no mês {int(mes):02d}/{int(ano)}."

    if destino:
        dest = _exigir_fora_do_c(Path(destino))
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        emp = _rotulo_empresa_pasta(g, cnpj)
        dest_dir = _exigir_fora_do_c(
            pasta_dados_garimpeiro() / "DominioInutilizadas" / emp
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _DOMINIO_INUT_NOME_FICHEIRO
    cab = "|".join([""] * _DOMINIO_INUT_N_CAMPOS)
    texto = cab + "\n" + "\n".join(linhas_dados) + "\n"
    dest.write_text(texto, encoding="latin-1")
    acum_u = str(acumulador or _DOMINIO_INUT_ACUM).strip() or _DOMINIO_INUT_ACUM
    cfop_u = str(cfop or _DOMINIO_INUT_CFOP).strip() or _DOMINIO_INUT_CFOP
    return (
        str(dest),
        len(linhas_dados),
        f"{len(linhas_dados)} inutilizada(s) → {dest}\n"
        f"Acumulador {acum_u} · CFOP {cfop_u}\n"
        f"Datas no mês {int(mes):02d}/{int(ano)} (dia da Sefaz, mês da competência).",
    )
