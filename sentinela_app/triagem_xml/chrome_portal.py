"""Abre o Google Chrome visível, em guia anônima, na tela de login do Portal Nacional."""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

URL_LOGIN = "https://www.nfse.gov.br/EmissorNacional/Login?ReturnUrl=%2femissornacional"


def caminho_do_chrome() -> str | None:
    if os.name != "nt":
        return None
    candidatos = []
    for chave in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(chave) or ""
        if base:
            candidatos.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    for caminho in candidatos:
        if os.path.isfile(caminho):
            return caminho
    return None


def motivo_sem_chrome() -> str:
    if os.name != "nt":
        return (
            "Neste servidor não há Google Chrome para o robô abrir a guia anônima. "
            "Abra o Portal Nacional no seu navegador, baixe as notas e importe aqui no Sentinela. "
            "Para o robô com guia anônima, rode o Sentinela no Windows com o Chrome instalado."
        )
    if not caminho_do_chrome():
        return "Não encontrei o Google Chrome neste computador. Instale o Chrome e clique em Iniciar de novo."
    return ""


def raiz_do_perfil() -> Path:
    raiz = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(raiz) / "Sentinela" / "chrome_apanhador"


def pasta_do_perfil() -> Path:
    pasta = raiz_do_perfil() / time.strftime("%Y%m%d%H%M%S")
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def montar_comando(chrome: str, perfil: str, porta: int | None = None) -> list[str]:
    if porta is None:
        porta = 0
    return [
        chrome,
        f"--remote-debugging-port={porta}",
        f"--user-data-dir={perfil}",
        "--incognito",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-networking",
        "--disable-blink-features=AutomationControlled",
        "--start-maximized",
        "--lang=pt-BR",
        "--new-window",
        URL_LOGIN,
    ]


class JanelaChrome:
    def __init__(self, proc, porta: int, perfil: Path, saida):
        self.proc = proc
        self.porta = porta
        self.perfil = perfil
        self._saida = saida

    @classmethod
    def abrir(cls, _pasta_dados=None) -> "JanelaChrome":
        chrome = caminho_do_chrome()
        if not chrome:
            raise RuntimeError(motivo_sem_chrome() or "Não encontrei o Google Chrome neste computador.")
        perfil = pasta_do_perfil()
        porta = porta_livre()
        saida = (perfil / "chrome_log.txt").open("w", encoding="utf-8", errors="replace")
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 1
        proc = subprocess.Popen(
            montar_comando(chrome, str(perfil), porta),
            stdout=saida,
            stderr=subprocess.STDOUT,
            startupinfo=startupinfo,
        )
        porta_ok = _esperar_cdp(proc, porta, perfil)
        if porta_ok is None:
            trecho = _trecho_log(perfil)
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            detalhe = f" {trecho}" if trecho else ""
            raise RuntimeError("O Chrome não abriu a janela de login em guia anônima." + detalhe)
        return cls(proc, porta_ok, perfil, saida)

    def conectar(self, playwright):
        navegador = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.porta}")
        contexto = _escolher_contexto(navegador)
        if contexto is None:
            raise RuntimeError("O Chrome abriu na tela de login, mas o robô não encontrou a guia anônima.")
        proc = self.proc

        class _Contexto:
            def __getattr__(self, nome):
                return getattr(contexto, nome)

            def close(self):
                try:
                    navegador.close()
                except Exception:
                    pass
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except OSError:
                        pass

        return _Contexto()


def _escolher_contexto(navegador):
    """Prioriza o contexto da guia anônima que já tem a página do Portal Nacional."""
    for _ in range(40):
        abertos = list(navegador.contexts)
        com_portal = []
        com_pagina = []
        for item in abertos:
            paginas = list(item.pages)
            if not paginas:
                continue
            com_pagina.append(item)
            for pagina in paginas:
                url = (pagina.url or "").lower()
                if "nfse.gov.br" in url or "emissornacional" in url:
                    com_portal.append(item)
                    break
        if com_portal:
            return com_portal[-1]
        if com_pagina:
            return com_pagina[-1]
        if abertos:
            # Em --incognito o CDP às vezes expõe o contexto vazio primeiro; espera a guia.
            time.sleep(0.25)
            continue
        time.sleep(0.25)
    return None


def _porta_responde(porta: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", porta), timeout=0.25):
            return True
    except OSError:
        return False


def _esperar_cdp(proc, porta: int, perfil: Path) -> int | None:
    arquivo = perfil / "DevToolsActivePort"
    for _ in range(100):
        if proc.poll() is not None:
            return None
        if _porta_responde(porta):
            return porta
        if arquivo.is_file():
            try:
                linhas = arquivo.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                linhas = []
            if linhas and linhas[0].strip().isdigit():
                porta_arquivo = int(linhas[0].strip())
                if _porta_responde(porta_arquivo):
                    return porta_arquivo
        time.sleep(0.25)
    return None


def _trecho_log(perfil: Path) -> str:
    try:
        texto = (perfil / "chrome_log.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return texto[-400:]
