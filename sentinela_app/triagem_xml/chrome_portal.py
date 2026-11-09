"""Abre o Google Chrome visível na tela de login do Portal Nacional."""
from __future__ import annotations

import os
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
            "A janela de login do Portal Nacional foi aberta no seu navegador. "
            "Entre com o Certificado Digital ou com usuário e senha para baixar as notas e importar aqui no Sentinela."
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


def montar_comando(chrome: str, perfil: str) -> list[str]:
    return [
        chrome,
        "--incognito",
        "--remote-debugging-port=0",
        f"--user-data-dir={perfil}",
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
        saida = (perfil / "chrome_log.txt").open("w", encoding="utf-8", errors="replace")
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 1
        proc = subprocess.Popen(
            montar_comando(chrome, str(perfil)),
            stdout=saida,
            stderr=subprocess.STDOUT,
            startupinfo=startupinfo,
        )
        porta = _esperar_porta(proc, perfil)
        if porta is None:
            trecho = _trecho_log(perfil)
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            detalhe = f" {trecho}" if trecho else ""
            raise RuntimeError("O Chrome não abriu a janela de login." + detalhe)
        return cls(proc, porta, perfil, saida)

    def conectar(self, playwright):
        navegador = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{self.porta}")
        contexto = None
        for _ in range(20):
            abertos = list(navegador.contexts)
            for item in abertos:
                if item.pages:
                    contexto = item
                    break
            if contexto is None and abertos:
                contexto = abertos[0]
            if contexto is not None:
                break
            time.sleep(0.25)
        if contexto is None:
            raise RuntimeError("O Chrome abriu na tela de login, mas o robô não encontrou a janela.")
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


def _esperar_porta(proc, perfil: Path) -> int | None:
    arquivo = perfil / "DevToolsActivePort"
    for _ in range(80):
        if proc.poll() is not None:
            return None
        if arquivo.is_file():
            try:
                linhas = arquivo.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                linhas = []
            if linhas and linhas[0].strip().isdigit():
                return int(linhas[0].strip())
        time.sleep(0.25)
    return None


def _trecho_log(perfil: Path) -> str:
    try:
        texto = (perfil / "chrome_log.txt").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return texto[-400:]
