"""Ponte Sentinela ↔ app Windows “Baixar NFS-e” (CECHINEL / Microsoft Store).

Não embute o app de terceiros: só detecta, abre e importa os XMLs que ele
grava em C:\\Baixar NFSe\\XML\\<empresa>\\AAAA\\MM\\Recebidas|Emitidas\\.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from pathlib import Path

from clientes.competencia import interpretar
from clientes.models import Empresa
from triagem_xml.coletor_nfse import importar_xml_nfse

APP_FAMILY = "CECHINEL.BaixarNFS-e_r6k1qpfsymxsc"
APP_ID = f"{APP_FAMILY}!App110"
SHELL_APPS_FOLDER = f"shell:AppsFolder\\{APP_ID}"
PASTA_PADRAO_XML = Path(r"C:\Baixar NFSe\XML")
PASTA_PADRAO_DADOS = Path(r"C:\Baixar NFSe\Dados")
TIPOS_PASTA_IMPORTAVEIS = {"recebidas", "emitidas", "prestadas", "tomadas", "intermediadas"}


def _normalizar_nome(texto: str) -> str:
    bruto = unicodedata.normalize("NFKD", (texto or "").strip().upper())
    sem_acento = "".join(ch for ch in bruto if not unicodedata.combining(ch))
    return re.sub(r"[^A-Z0-9]+", "", sem_acento)


def pasta_xml_raiz() -> Path:
    env = (os.environ.get("SENTINELA_BAIXAR_NFSE_XML") or "").strip()
    if env:
        return Path(env)
    return PASTA_PADRAO_XML


def baixar_nfse_instalado() -> bool:
    """True se o pacote Store estiver instalado nesta máquina Windows."""
    if os.name != "nt":
        return False
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-AppxPackage -Name CECHINEL.BaixarNFS-e).PackageFullName",
            ],
            capture_output=True,
            text=True,
            timeout=12,
            creationflags=flags,
        )
        return bool((proc.stdout or "").strip())
    except Exception:
        return False


def pasta_dados_existe() -> bool:
    return PASTA_PADRAO_DADOS.is_dir() or pasta_xml_raiz().is_dir()


def abrir_baixar_nfse() -> tuple[bool, str]:
    """Abre o app Baixar NFS-e nesta máquina (só faz sentido no Windows local)."""
    if os.name != "nt":
        return False, "Abrir o Baixar NFS-e só funciona no Windows local (não no servidor Render)."
    try:
        # Preferência: AppsFolder (abre o app Store já instalado).
        subprocess.Popen(["explorer.exe", SHELL_APPS_FOLDER])
        return True, "Baixar NFS-e acionado no Windows."
    except Exception as exc:
        return False, f"Não foi possível abrir o Baixar NFS-e: {exc}"


def script_atalho_bat() -> str:
    """Conteúdo de um .bat para o usuário baixar e abrir o app no PC dele (ex.: quando o site está no Render)."""
    return (
        "@echo off\r\n"
        "title Abrir Baixar NFS-e\r\n"
        f"explorer.exe {SHELL_APPS_FOLDER}\r\n"
        "if errorlevel 1 (\r\n"
        "  echo Nao encontrei o app. Instale o Baixar NFS-e na Microsoft Store.\r\n"
        "  pause\r\n"
        ")\r\n"
    )


def localizar_pasta_empresa(empresa: Empresa, raiz: Path | None = None) -> Path | None:
    """Encontra a pasta da empresa dentro de C:\\Baixar NFSe\\XML pelo nome/fantasia/CNPJ."""
    base = raiz or pasta_xml_raiz()
    if not base.is_dir():
        return None

    alvos = {
        _normalizar_nome(empresa.razao_social),
        _normalizar_nome(getattr(empresa, "nome_fantasia", "") or ""),
        _normalizar_nome(empresa.codigo_dominio or ""),
    }
    alvos.discard("")
    cnpj = re.sub(r"\D", "", empresa.cnpj or "")

    pastas = [p for p in base.iterdir() if p.is_dir()]
    for pasta in pastas:
        nome_n = _normalizar_nome(pasta.name)
        if nome_n in alvos:
            return pasta
        for alvo in alvos:
            if alvo and (alvo in nome_n or nome_n in alvo):
                return pasta
        if cnpj and cnpj in pasta.name:
            return pasta
    return None


def listar_xmls_competencia(empresa: Empresa, competencia: str, raiz: Path | None = None) -> list[Path]:
    """Lista XMLs Recebidas/Emitidas do mês da competência ativa na pasta do Baixar NFS-e."""
    partes = interpretar(competencia)
    if partes is None:
        return []
    mes, ano = partes
    pasta_emp = localizar_pasta_empresa(empresa, raiz)
    if pasta_emp is None:
        return []

    pasta_mes = pasta_emp / f"{ano:04d}" / f"{mes:02d}"
    if not pasta_mes.is_dir():
        return []

    encontrados: list[Path] = []
    for sub in pasta_mes.iterdir():
        if not sub.is_dir():
            continue
        if sub.name.strip().lower() not in TIPOS_PASTA_IMPORTAVEIS:
            continue
        encontrados.extend(sorted(sub.glob("*.xml")))
    return encontrados


def importar_xmls_baixar_nfse(empresa: Empresa, competencia: str, raiz: Path | None = None) -> dict:
    """Importa XMLs do Baixar NFS-e para o cliente/competência do Sentinela."""
    base = raiz or pasta_xml_raiz()
    if not base.is_dir():
        return {
            "ok": False,
            "importadas": 0,
            "ignoradas": 0,
            "total": 0,
            "pasta_empresa": "",
            "erro": f"Pasta do Baixar NFS-e não encontrada: {base}",
        }

    pasta_emp = localizar_pasta_empresa(empresa, base)
    if pasta_emp is None:
        return {
            "ok": False,
            "importadas": 0,
            "ignoradas": 0,
            "total": 0,
            "pasta_empresa": "",
            "erro": (
                f"Não achei pasta de «{empresa.razao_social}» em {base}. "
                "No Baixar NFS-e, baixe as notas desta empresa e tente de novo."
            ),
        }

    arquivos = listar_xmls_competencia(empresa, competencia, base)
    importadas = 0
    ignoradas = 0
    for caminho in arquivos:
        lida = importar_xml_nfse(empresa, competencia, caminho)
        if lida is not None:
            importadas += 1
        else:
            ignoradas += 1

    return {
        "ok": importadas > 0 or (not arquivos),
        "importadas": importadas,
        "ignoradas": ignoradas,
        "total": len(arquivos),
        "pasta_empresa": str(pasta_emp),
        "erro": "" if arquivos else f"Nenhum XML na competência em {pasta_emp}.",
    }
