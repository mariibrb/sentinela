"""
Pastas na partilha GARIMPEIRO.

O utilizador só precisa de ver:
  Autenticidade, Fechamento, Relatórios

Arquivo (XML já lidos) é pasta interna — fica oculta.

O resto (estrutura interna) fica em _sistema, oculto.
Para ver essas pastas no Explorador: senha Senhaforte@123.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

SENHA_PASTAS_SISTEMA = "Senhaforte@123"

NOME_ARQUIVO = "Arquivo"
NOME_ARQUIVO_LEGADO = "ArquivoFiscal"
NOME_FECHAMENTO = "Fechamento"
NOME_FECHAMENTO_LEGADO = "Contabilidade"
NOME_RELATORIOS = "Relatórios"
NOME_RELATORIOS_ASCII = "Relatorios"
NOME_EXCEL_LEGADO = "Excel"
NOME_AUTENTICIDADE = "Autenticidade"
NOME_SISTEMA = "_sistema"

NOMES_ARQUIVO = (NOME_ARQUIVO, NOME_ARQUIVO_LEGADO)
NOMES_FECHAMENTO = (NOME_FECHAMENTO, NOME_FECHAMENTO_LEGADO)
NOMES_RELATORIOS = (NOME_RELATORIOS, NOME_RELATORIOS_ASCII, NOME_EXCEL_LEGADO)

PASTAS_VISIVEIS = (
    NOME_AUTENTICIDADE,
    NOME_FECHAMENTO,
    NOME_RELATORIOS,
)

# Sobras antigas no raiz da partilha — ocultar, não apagar.
_OCULTAR_NO_RAIZ = (
    NOME_ARQUIVO,
    NOME_ARQUIVO_LEGADO,
    NOME_SISTEMA,
    "_utilizadores",
    NOME_FECHAMENTO_LEGADO,
    NOME_EXCEL_LEGADO,
    "Importar",
    "SIEG_Importar",
    "temp_garimpo_zips",
    "temp_garimpo_uploads",
    "temp_windows_ambiente",
    "pip_cache_local",
    "relatorio_index",
    "config_v2.json",
    "LEIA-ME.txt",
)

_OCULTAR_DENTRO_ARQUIVO = (
    "ZIP",
    "_inativas",
    "_log_catalogo_empresas",
    NOME_EXCEL_LEGADO,
    NOME_AUTENTICIDADE,
    NOME_FECHAMENTO_LEGADO,
    "Importar",
)

FILE_ATTRIBUTE_READONLY = 0x1
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_NORMAL = 0x80


def hash_senha_pastas_sistema(senha: str) -> str:
    return hashlib.sha256(str(senha or "").encode("utf-8")).hexdigest()


def verificar_senha_pastas_sistema(senha: str) -> bool:
    return hash_senha_pastas_sistema(senha) == hash_senha_pastas_sistema(
        SENHA_PASTAS_SISTEMA
    )


def _chave_partilha_letra(p: str | Path) -> str:
    """Compara UNC e P: como o mesmo sítio (sem Path.resolve())."""
    s = str(p or "").strip().replace("/", "\\").lower().rstrip("\\")
    s = s.replace(r"\\10.10.100.201\xml\garimpeiro", r"p:\garimpeiro")
    s = s.replace(r"\\10.10.100.201\xml", "p:")
    return s


def pasta_sieg_e_da_partilha(path: str, raiz_partilha: Path) -> bool:
    """True se o caminho SIEG aponta para a partilha GARIMPEIRO (não deve ser usado)."""
    p = str(path or "").strip()
    if not p:
        return False
    rp = _chave_partilha_letra(p)
    raiz = _chave_partilha_letra(raiz_partilha)
    return rp == raiz or (raiz and rp.startswith(raiz + "\\"))


def data_root_de_arquivo(pasta_arquivo: Path) -> Path:
    """Raiz GARIMPEIRO (irmã de Arquivo / ArquivoFiscal)."""
    n = Path(pasta_arquivo).name.lower()
    if n in ("arquivo", "arquivofiscal"):
        return Path(pasta_arquivo).parent
    return Path(pasta_arquivo)


def _primeiro_existente(raiz: Path, nomes: tuple[str, ...]) -> Path | None:
    for nome in nomes:
        p = raiz / nome
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return None


def _juntar(link: Path, alvo: Path) -> bool:
    """Nome visível = junção local, ou cópia na partilha (mklink /J não funciona em P:)."""
    if link.exists():
        return link.is_dir()
    if not alvo.is_dir():
        return False
    try:
        os.symlink(str(alvo), str(link), target_is_directory=True)
        return link.exists()
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(alvo)],
                capture_output=True,
                text=True,
                creationflags=flags,
            )
            if r.returncode == 0 and link.exists():
                return True
        except OSError:
            pass
    try:
        shutil.copytree(str(alvo), str(link), dirs_exist_ok=True)
        return link.is_dir()
    except OSError:
        return False


def _pasta_tem_empresas_fiscais(p: Path) -> bool:
    """True se a pasta é o arquivo de XMLs (sqlite/catálogo), não um dump de exportação."""
    if not p.is_dir():
        return False
    if (p / "_catalogo_empresas.json").is_file():
        return True
    try:
        n = 0
        for item in p.iterdir():
            if item.is_dir() and (item / "arquivo.sqlite").is_file():
                return True
            n += 1
            if n >= 8:
                break
    except OSError:
        return False
    return False


def pasta_arquivo(raiz: Path, *, criar: bool = False) -> Path:
    """Pasta visível Arquivo (XMLs). Não usa ArquivoFiscal se lá só houver exportações."""
    raiz = Path(raiz)
    vis = raiz / NOME_ARQUIVO
    if vis.is_dir() and _pasta_tem_empresas_fiscais(vis):
        return vis
    legado = raiz / NOME_ARQUIVO_LEGADO
    if legado.is_dir() and _pasta_tem_empresas_fiscais(legado):
        if vis.is_dir() and not _pasta_tem_empresas_fiscais(vis):
            try:
                vis.rmdir()
            except OSError:
                pass
        _juntar(vis, legado)
        if vis.is_dir() and _pasta_tem_empresas_fiscais(vis):
            return vis
        return legado
    if criar:
        vis.mkdir(parents=True, exist_ok=True)
        if legado.is_dir() and not _pasta_tem_empresas_fiscais(legado):
            ocultar_pasta(legado)
    if vis.is_dir():
        return vis
    return legado if legado.is_dir() else vis


def pasta_autenticidade(raiz: Path, *, criar: bool = False) -> Path:
    dest = Path(raiz) / NOME_AUTENTICIDADE
    if criar:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def pasta_fechamento(raiz: Path, *, criar: bool = False) -> Path:
    atual = _primeiro_existente(Path(raiz), NOMES_FECHAMENTO)
    if atual is not None:
        if atual.name != NOME_FECHAMENTO:
            _juntar(Path(raiz) / NOME_FECHAMENTO, atual)
        vis = Path(raiz) / NOME_FECHAMENTO
        return vis if vis.is_dir() else atual
    dest = Path(raiz) / NOME_FECHAMENTO
    if criar:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def pasta_relatorios(raiz: Path, *, criar: bool = False) -> Path:
    atual = _primeiro_existente(Path(raiz), NOMES_RELATORIOS)
    if atual is not None:
        if atual.name != NOME_RELATORIOS:
            _juntar(Path(raiz) / NOME_RELATORIOS, atual)
        vis = Path(raiz) / NOME_RELATORIOS
        if vis.is_dir():
            if criar:
                vis.mkdir(parents=True, exist_ok=True)
            return vis
        if criar:
            atual.mkdir(parents=True, exist_ok=True)
        return atual
    dest = Path(raiz) / NOME_RELATORIOS
    if criar:
        dest.mkdir(parents=True, exist_ok=True)
    return dest


def pasta_sistema(raiz: Path, *, criar: bool = False) -> Path:
    dest = Path(raiz) / NOME_SISTEMA
    if criar:
        dest.mkdir(parents=True, exist_ok=True)
        aviso = dest / "NAO_APAGAR.txt"
        if not aviso.is_file():
            try:
                aviso.write_text(
                    "Pasta interna do Garimpeiro. Não apague.\n"
                    "Para ver esta pasta no Explorador, use Ajustes → Mostrar pastas do sistema\n"
                    "(senha da Mariana).\n",
                    encoding="utf-8",
                )
            except OSError:
                pass
    return dest


def _attrs_win(path: Path) -> int | None:
    if sys.platform != "win32":
        return None
    try:
        return int(ctypes_get_attrs(str(path)))
    except OSError:
        return None


def ctypes_get_attrs(path: str) -> int:
    import ctypes

    GetFileAttributesW = ctypes.windll.kernel32.GetFileAttributesW
    GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    GetFileAttributesW.restype = ctypes.c_uint32
    val = GetFileAttributesW(path)
    if val == 0xFFFFFFFF:
        raise OSError("GetFileAttributesW")
    return int(val)


def ctypes_set_attrs(path: str, attrs: int) -> None:
    import ctypes

    SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
    SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    SetFileAttributesW.restype = ctypes.c_int
    if not SetFileAttributesW(path, attrs):
        raise OSError("SetFileAttributesW")


def ocultar_pasta(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform != "win32":
        return
    try:
        atual = ctypes_get_attrs(str(path))
        ctypes_set_attrs(
            str(path),
            (atual | FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM)
            & ~FILE_ATTRIBUTE_NORMAL,
        )
    except OSError:
        pass


def mostrar_pasta(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform != "win32":
        return
    try:
        atual = ctypes_get_attrs(str(path))
        novo = atual & ~FILE_ATTRIBUTE_HIDDEN & ~FILE_ATTRIBUTE_SYSTEM
        if novo == 0:
            novo = FILE_ATTRIBUTE_NORMAL
        ctypes_set_attrs(str(path), novo)
    except OSError:
        pass


def _ocultar_internos_arquivo(pasta_arq: Path, *, empresas: bool = False) -> None:
    if not pasta_arq.is_dir():
        return
    for nome in _OCULTAR_DENTRO_ARQUIVO:
        ocultar_pasta(pasta_arq / nome)
    if not empresas:
        return
    try:
        for emp in pasta_arq.iterdir():
            if not emp.is_dir() or emp.name.startswith("_"):
                continue
            for nome in ("ZIP", NOME_EXCEL_LEGADO, NOME_AUTENTICIDADE):
                ocultar_pasta(emp / nome)
    except OSError:
        pass


def garantir_pastas_visiveis(raiz: Path) -> dict[str, str]:
    """Cria Autenticidade / Fechamento / Relatórios (e Arquivo interno)."""
    raiz = Path(raiz)
    raiz.mkdir(parents=True, exist_ok=True)
    arq = pasta_arquivo(raiz, criar=True)
    aut = pasta_autenticidade(raiz, criar=True)
    fech = pasta_fechamento(raiz, criar=True)
    rel = pasta_relatorios(raiz, criar=True)
    pasta_sistema(raiz, criar=True)
    return {
        "arquivo": str(arq),
        "autenticidade": str(aut),
        "fechamento": str(fech),
        "relatorios": str(rel),
        "sistema": str(pasta_sistema(raiz)),
    }


def proteger_pastas_sistema(raiz: Path) -> dict[str, str]:
    """Oculta estrutura interna. Pastas visíveis ficam à vista."""
    raiz = Path(raiz)
    out = garantir_pastas_visiveis(raiz)
    pasta_arq = Path(out["arquivo"])
    # Não ocultar ArquivoFiscal/Contabilidade/Excel se forem o próprio sítio dos dados
    # (junção Relatórios→Excel no Windows esconderia as duas).

    for nome in _OCULTAR_NO_RAIZ:
        p = raiz / nome
        if nome in PASTAS_VISIVEIS:
            continue
        ocultar_pasta(p)

    ocultar_pasta(pasta_arq)
    ocultar_pasta(raiz / NOME_ARQUIVO_LEGADO)
    ocultar_pasta(Path(out["sistema"]))
    _ocultar_internos_arquivo(pasta_arq)
    for nome in PASTAS_VISIVEIS:
        mostrar_pasta(raiz / nome)
    for novo, velho in (
        (NOME_FECHAMENTO, NOME_FECHAMENTO_LEGADO),
        (NOME_RELATORIOS, NOME_EXCEL_LEGADO),
        (NOME_RELATORIOS_ASCII, NOME_EXCEL_LEGADO),
    ):
        if (raiz / novo).is_dir() and (raiz / velho).exists():
            ocultar_pasta(raiz / velho)

    leia = raiz / "LEIA-ME.txt"
    if leia.exists():
        try:
            leia.unlink()
        except OSError:
            ocultar_pasta(leia)
    return out


def desbloquear_pastas_sistema(raiz: Path, senha: str) -> tuple[bool, str, str]:
    """Mostra a pasta Arquivo (e _sistema) no Explorador se a senha estiver certa."""
    if not verificar_senha_pastas_sistema(senha):
        return False, "Senha incorreta.", ""
    raiz = Path(raiz)
    arq = pasta_arquivo(raiz, criar=True)
    mostrar_pasta(arq)
    dest = pasta_sistema(raiz, criar=True)
    mostrar_pasta(dest)
    legado = raiz / NOME_ARQUIVO_LEGADO
    if legado.is_dir():
        mostrar_pasta(legado)
    return (
        True,
        f"A pasta Arquivo ficou visível no Explorador: {arq}. "
        "Fica oculta outra vez ao reiniciar o Garimpeiro ou se guardar senha em branco.",
        str(arq),
    )


def bloquear_pastas_sistema(raiz: Path) -> None:
    proteger_pastas_sistema(raiz)
