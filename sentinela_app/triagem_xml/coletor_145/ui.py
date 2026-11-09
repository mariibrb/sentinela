"""Janela do Coletor com escolha entre serviços tomados e prestados."""

from __future__ import annotations

import base64
import gzip
import os
import re
import shutil
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.bootstrap import pasta_dados, raiz_app
from app.excel import gravar_planilha, nome_arquivo
from app.periodo import MESES, periodo_do_mes, periodo_livre
from app.version import APP_MUTEX, APP_TITLE, APP_USER_MODEL_ID, __version__

FUNDO = "#f4f6fb"
AZUL = "#0b3d91"
VERDE = "#0f766e"
TEXTO = "#334155"

_ESTADO: dict = {}

_JS_CLICAR_EMITIDAS = r"""
() => {
  const attr = (el) => [
    el.getAttribute("title"),
    el.getAttribute("data-original-title"),
    el.getAttribute("data-bs-original-title"),
    el.getAttribute("aria-label"),
    el.getAttribute("alt"),
    el.getAttribute("href"),
    el.getAttribute("src"),
  ].filter(Boolean).join(" ");

  const hrefs = [...document.querySelectorAll("a[href*='Emitidas'], a[href*='emitidas']")];
  if (hrefs.length) {
    hrefs[0].click();
    return hrefs[0].getAttribute("href") || "href Emitidas";
  }

  const re = /emitidas/i;
  const clicaveis = [...document.querySelectorAll("a, button, [role='link'], [role='button']")];
  for (const el of clicaveis) {
    const t = attr(el);
    if (!re.test(t)) continue;
    el.click();
    return t || "Emitidas";
  }

  const marcaCima = /arrow-up|seta.*cima|fa-arrow-up/i;
  const filhos = [...document.querySelectorAll("img, i, span, svg, em")];
  for (const el of filhos) {
    const t = attr(el) + " " + (el.className || "");
    if (!marcaCima.test(t)) continue;
    const alvo = el.closest("a, button") || el;
    alvo.click();
    return attr(alvo) || "ícone Emitidas";
  }

  const visiveisTopo = [...document.querySelectorAll("header a, .navbar a, nav a, .navbar-right a")]
    .filter((a) => {
      const r = a.getBoundingClientRect();
      return r.width >= 16 && r.height >= 16 && r.top >= 0 && r.top < 140 && r.right > window.innerWidth * 0.35;
    })
    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  const setaCima = visiveisTopo.find((a) => marcaCima.test(attr(a) + " " + a.innerHTML) || /emitid/i.test(attr(a)));
  if (setaCima) {
    setaCima.click();
    return "NFS-e Emitidas";
  }
  const setaBaixo = visiveisTopo.findIndex((a) => /arrow-down|seta.*baix|fa-arrow-down|recebid/i.test(attr(a) + " " + a.innerHTML));
  if (setaBaixo > 0) {
    visiveisTopo[setaBaixo - 1].click();
    return "NFS-e Emitidas";
  }
  if (visiveisTopo.length >= 3) {
    visiveisTopo[2].click();
    return "3º ícone do topo (NFS-e Emitidas)";
  }
  return "";
}
"""

_MAPAS_PRESTADOS = {
    "_na_recebidas": {
        "recebidas": "emitidas",
    },
    "_clicar_icone_recebidas": {
        "Cliquei no ícone de NFS-e Recebidas (": "Cliquei no ícone de NFS-e Emitidas (",
        "Notas/Recebidas": "Notas/Emitidas",
        "recebidas": "emitidas",
    },
    "_ir_para_recebidas": {
        "recebidas": "emitidas",
        "Estou no Dashboard. As notas recebidas não ficam nessa tela — vou abrir NFS-e Recebidas.": "Estou no Dashboard. As notas emitidas não ficam nessa tela — vou abrir NFS-e Emitidas.",
        "Abrindo NFS-e Recebidas: ": "Abrindo NFS-e Emitidas: ",
        "Não abri pelo endereço (": "Não abri pelo endereço (",
        "falha ao abrir Recebidas": "falha ao abrir Emitidas",
        "Não abri a tela de NFS-e Recebidas: ": "Não abri a tela de NFS-e Emitidas: ",
        "Notas/Recebidas": "Notas/Emitidas",
        "Tela de NFS-e Recebidas aberta.": "Tela de NFS-e Emitidas aberta.",
        "Voltei para o login ao abrir NFS-e Recebidas. Faça o login de novo no Chrome.": "Voltei para o login ao abrir NFS-e Emitidas. Faça o login de novo no Chrome.",
        "Ainda no Dashboard; clicando no 4º ícone do topo e tentando o endereço de novo…": "Ainda no Dashboard; tentando o endereço de novo…",
        "não chegou em Recebidas": "não chegou em Emitidas",
        "Não cheguei em /EmissorNacional/Notas/Recebidas. Página atual: ": "Não cheguei em /EmissorNacional/Notas/Emitidas. Página atual: ",
    },
    "_na_lista_recebidas": {
        "recebidas": "emitidas",
    },
    "_alvo_lista_recebidas": {
        "recebidas": "emitidas",
    },
    "_coletar_periodo": {
        "recebidas": "emitidas",
        "A listagem de recebidas ainda não abriu; não vou clicar nas notas do Dashboard. Página atual: ": "A listagem de emitidas ainda não abriu; não vou clicar nas notas do Dashboard. Página atual: ",
    },
    "aguardar_login": {
        "Painel detectado. Indo para NFS-e Recebidas…": "Painel detectado. Indo para NFS-e Emitidas…",
    },
    "_fallback_da_linha": {
        "emitida por": "tomador",
        "prestador": "tomado por",
        "emitente": "tomador",
    },
    "gravar_planilha": {
        "NFS-e Recebidas": "NFS-e Prestadas",
        "NFS-e Recebidas — ": "NFS-e Prestadas — ",
    },
    "nome_arquivo": {
        "Relatório NFS-e Tomadas - Portal Nacional.xlsx": "Relatório NFS-e Prestadas - Portal Nacional.xlsx",
    },
}


def _base_consts(fn):
    chave = fn.__module__ + "." + fn.__qualname__
    guardadas = _ESTADO.setdefault("consts", {})
    if chave not in guardadas:
        guardadas[chave] = fn.__code__.co_consts
    return guardadas[chave]


def _aplicar_mapa(fn, mapa):
    base = _base_consts(fn)
    novos = tuple(mapa.get(item, item) if isinstance(item, str) else item for item in base)
    if novos != fn.__code__.co_consts:
        fn.__code__ = fn.__code__.replace(co_consts=novos)


def aplicar_tipo(tipo: str) -> None:
    """Aponta a coleta para notas recebidas (seta baixo) ou emitidas (seta cima)."""
    import re

    import app.excel as excel
    import app.portal as portal

    prestado = tipo == "prestados"
    if "url" not in _ESTADO:
        _ESTADO["url"] = portal.URL_RECEBIDAS
        _ESTADO["js_click"] = portal._JS_CLICAR_RECEBIDAS
        _ESTADO["js_extrair"] = portal._JS_EXTRAIR
        _ESTADO["cab"] = tuple(excel.CABECALHO)

    portal.URL_RECEBIDAS = (
        "https://www.nfse.gov.br/EmissorNacional/Notas/Emitidas" if prestado else _ESTADO["url"]
    )
    portal._JS_CLICAR_RECEBIDAS = _JS_CLICAR_EMITIDAS if prestado else _ESTADO["js_click"]
    portal.RE_RECEBIDAS = re.compile(r"NFS-e\s+Emitidas" if prestado else r"NFS-e\s+Recebidas", re.I)

    js = _ESTADO["js_extrair"]
    antigo = 'valorNoPainel("Emitente", ["Razão Social", "Razao Social"])'
    novo = 'valorNoPainel("Tomador", ["Razão Social", "Razao Social", "Nome / Razão Social", "Nome"])'
    if prestado and antigo in js:
        portal._JS_EXTRAIR = js.replace(antigo, novo, 1)
    else:
        portal._JS_EXTRAIR = js

    cab = list(_ESTADO["cab"])
    for indice, coluna in enumerate(cab):
        if coluna == "Razão_Emitente":
            cab[indice] = "Razão do Tomador" if prestado else "Razão_Emitente"
    excel.CABECALHO = tuple(cab)

    alvos = {
        "_na_recebidas": portal._na_recebidas,
        "_clicar_icone_recebidas": portal._clicar_icone_recebidas,
        "_ir_para_recebidas": portal._ir_para_recebidas,
        "_na_lista_recebidas": portal._na_lista_recebidas,
        "_alvo_lista_recebidas": portal._alvo_lista_recebidas,
        "_coletar_periodo": portal._coletar_periodo,
        "aguardar_login": portal.aguardar_login,
        "_fallback_da_linha": portal._fallback_da_linha,
        "gravar_planilha": excel.gravar_planilha,
        "nome_arquivo": excel.nome_arquivo,
    }
    for nome, fn in alvos.items():
        _aplicar_mapa(fn, _MAPAS_PRESTADOS[nome] if prestado else {})


def _sessao_pronta(page) -> bool:
    """Login por senha cai numa home sem /dashboard. Certificado cai no painel."""
    import app.portal as portal

    try:
        if page is None or page.is_closed():
            return False
    except Exception:
        return False
    try:
        url = portal._url_da_pagina(page)
    except Exception:
        try:
            url = (page.url or "").lower()
        except Exception:
            return False
    if "nfse.gov.br" not in url or "/emissornacional" not in url:
        return False
    if "/login" in url or "acesso.gov.br" in url or "sso.acesso" in url:
        return False
    if "/dashboard" in url or "/notas/" in url:
        return True
    try:
        if page.locator("input[type='password']").count() > 0:
            return False
    except Exception:
        pass
    try:
        escolha = page.get_by_text(re.compile(r"Acesso com Usuário/Senha|Acesso via GOVBR|Acesso com Certificado", re.I))
        if escolha.count() > 0 and escolha.first.is_visible():
            return False
    except Exception:
        pass
    return True


def _instalar_login_direto() -> None:
    import time

    import app.portal as portal

    if getattr(portal.aguardar_login, "_direto", False):
        return

    def aguardar_login(page, parar, confirmar, log, timeout_s=1200):
        inicio = time.time()
        visto = 0
        log("Faça o login no Chrome (senha ou certificado).")
        log("Quando o login terminar, o robô abre a lista sozinho. Não precisa clicar no ícone.")
        while time.time() - inicio < timeout_s:
            if parar.is_set():
                raise InterruptedError("Coleta interrompida.")
            atual = portal._pagina_do_painel(page.context, page)
            if _sessao_pronta(atual):
                visto += 1
                if visto >= 2:
                    try:
                        atual.bring_to_front()
                    except Exception:
                        pass
                    try:
                        atual.wait_for_timeout(800)
                    except Exception:
                        pass
                    log("Login confirmado.")
                    return atual
            else:
                visto = 0
            try:
                page.wait_for_timeout(1200)
            except Exception:
                time.sleep(1.2)
        raise TimeoutError("Tempo esgotado aguardando o login no portal.")

    aguardar_login._direto = True
    portal.aguardar_login = aguardar_login


def _liberar_planilha(destino) -> None:
    """Tira proteção e trava de célula, e garante o filtro. Prestados e tomados ficam iguais."""
    import os
    import stat

    from openpyxl import load_workbook
    from openpyxl.styles import Protection
    from openpyxl.utils import get_column_letter

    wb = load_workbook(destino)
    solta = Protection(locked=False, hidden=False)
    for ws in wb.worksheets:
        ws.protection.sheet = False
        ws.protection.disable()
        ws.sheet_state = "visible"
        ultima_linha = max(ws.max_row or 2, 2)
        ultima_col = max(ws.max_column or 1, 1)
        ws.auto_filter.ref = "A2:%s%s" % (get_column_letter(ultima_col), ultima_linha)
        if not ws.freeze_panes:
            ws.freeze_panes = "A3"
        for row in ws.iter_rows(min_row=1, max_row=ultima_linha, max_col=ultima_col):
            for cell in row:
                cell.protection = solta
    wb.save(destino)
    try:
        os.chmod(destino, stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass
    try:
        os.remove(str(destino) + ":Zone.Identifier")
    except OSError:
        pass


def _em_login(page) -> bool:
    try:
        if page is None or page.is_closed():
            return False
    except Exception:
        return False
    import app.portal as portal

    try:
        return bool(portal._na_tela_login(page))
    except Exception:
        return False


def _renovar_sessao(page, url_lista, log) -> None:
    import app.portal as portal

    estado = portal._RETOMADA
    log(
        "A sessão do portal expirou. Faça o login de novo no Chrome (senha ou certificado). "
        "A coleta continua desta nota, sem voltar ao início."
    )
    portal.aguardar_login(page, estado["parar"], estado["confirmar"], log)
    if not url_lista:
        return
    try:
        page.goto(url_lista, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass
    if _em_login(page):
        portal.aguardar_login(page, estado["parar"], estado["confirmar"], log)
        try:
            page.goto(url_lista, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass


def _chave_da_nota(nota) -> str:
    if not isinstance(nota, dict):
        return ""
    digitos = re.sub(r"\D", "", str(nota.get("chave_acesso") or ""))
    if len(digitos) >= 44:
        return digitos
    return ""


def _nome_da_nota(nota, chave: str) -> str:
    numero = str((nota or {}).get("numero") or "").strip() or chave[-8:]
    razao = str((nota or {}).get("razao_emitente") or "nota").strip()
    bruto = "%s - %s" % (numero, razao)
    limpo = re.sub(r'[<>:"/\\|?*]', " ", bruto)
    limpo = re.sub(r"\s+", " ", limpo).strip().rstrip(".")
    return (limpo or chave)[:80]


def _preparar_documentos(pasta_dados, tipo: str) -> None:
    import app.portal as portal

    portal._PASTA_DOCUMENTOS = Path(str(pasta_dados)) / "documentos" / tipo
    portal._DOCUMENTOS = {}
    portal._NOMES_DOCUMENTOS = set()
    portal._XML_PARADO = False
    portal._PDF_PARADO = False


def _parece_html(corpo: bytes) -> bool:
    amostra = corpo[:1200].lstrip().lower()
    return (
        amostra.startswith(b"<!doctype")
        or amostra.startswith(b"<html")
        or b"captcha" in amostra
        or b"hcaptcha" in amostra
        or b"<form" in amostra[:400]
    )


def _como_arquivo(corpo: bytes):
    if corpo.startswith(b"\x1f\x8b"):
        try:
            corpo = gzip.decompress(corpo)
        except Exception:
            return None
    if corpo.startswith(b"\xef\xbb\xbf"):
        corpo = corpo[3:]
    if corpo.startswith(b"%PDF"):
        return "pdf", corpo
    amostra = corpo[:800].lstrip().lower()
    if amostra.startswith(b"<?xml") or b"<nfse" in amostra or b":nfse" in amostra:
        return "xml", corpo
    return None


def _baixar_url(page, url: str):
    dados = page.evaluate(
        """async (url) => {
            const resposta = await fetch(url, {credentials: 'include', redirect: 'follow'});
            const buf = await resposta.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binario = '';
            for (let i = 0; i < bytes.length; i += 0x8000) {
                binario += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
            }
            return {
                status: resposta.status,
                tipo: resposta.headers.get('content-type') || '',
                b64: btoa(binario),
            };
        }""",
        url,
    )
    if not isinstance(dados, dict):
        return 0, b""
    status = int(dados.get("status") or 0)
    tipo = str(dados.get("tipo") or "").lower()
    corpo = base64.b64decode(dados.get("b64") or "")
    if status != 200 or not corpo or _parece_html(corpo) or ("text/html" in tipo and _como_arquivo(corpo) is None):
        return status, b""
    return status, corpo


def _urls_da_nota(page, chave: str):
    hrefs = []
    try:
        hrefs = page.evaluate("() => [...document.querySelectorAll('a[href]')].map((a) => a.href)")
    except Exception:
        hrefs = []
    xmls = []
    pdfs = []
    for href in hrefs or []:
        texto = str(href)
        baixo = texto.lower()
        if chave not in texto or "visualizar" in baixo:
            continue
        if "danfse" in baixo or baixo.endswith(".pdf"):
            pdfs.append(texto)
        elif "xml" in baixo:
            xmls.append(texto)
    base = "https://www.nfse.gov.br/EmissorNacional/Notas/Download/"
    xmls.append(base + "XML/" + chave)
    pdfs.append(base + "DANFSe/" + chave)
    return xmls, pdfs


def _guardar_tipo(page, urls, pasta: Path, nome: str, extensao: str):
    import app.portal as portal

    if extensao == "xml" and portal._XML_PARADO:
        return None
    if extensao == "pdf" and portal._PDF_PARADO:
        return None
    for url in urls:
        try:
            _status, corpo = _baixar_url(page, url)
        except Exception:
            corpo = b""
        if not corpo:
            continue
        identificado = _como_arquivo(corpo)
        if identificado is None or identificado[0] != extensao:
            continue
        pasta.mkdir(parents=True, exist_ok=True)
        destino = pasta / ("%s.%s" % (nome, extensao))
        destino.write_bytes(identificado[1])
        return destino
    if extensao == "xml":
        portal._XML_PARADO = True
    else:
        portal._PDF_PARADO = True
    return None


def _baixar_documentos(page, notas, parar, log) -> None:
    import app.portal as portal

    if page is None or not notas or (portal._XML_PARADO and portal._PDF_PARADO):
        return
    pasta = Path(portal._PASTA_DOCUMENTOS)
    log("Baixando XML e PDF das %s nota(s) desta parte…" % len(notas))
    for nota in notas:
        if parar.is_set() or (portal._XML_PARADO and portal._PDF_PARADO):
            break
        chave = _chave_da_nota(nota)
        if not chave or chave in portal._DOCUMENTOS:
            continue
        nome = _nome_da_nota(nota, chave)
        base_nome = nome
        n = 2
        while nome.lower() in portal._NOMES_DOCUMENTOS:
            nome = "%s (%s)" % (base_nome, n)
            n += 1
        portal._NOMES_DOCUMENTOS.add(nome.lower())
        xmls, pdfs = _urls_da_nota(page, chave)
        try:
            caminho_xml = _guardar_tipo(page, xmls, pasta / "xml", nome, "xml")
            caminho_pdf = _guardar_tipo(page, pdfs, pasta / "pdf", nome, "pdf")
        except Exception as e:
            log("Nota %s: não baixei os arquivos (%s). O relatório dela continua." % (nota.get("numero") or chave[-6:], str(e).split("\n", 1)[0]))
            continue
        if caminho_xml is None and caminho_pdf is None:
            log(
                "Nota %s: o portal não entregou o XML nem o PDF. O relatório segue, e paro de tentar os arquivos nesta execução."
                % (nota.get("numero") or chave[-6:])
            )
            continue
        portal._DOCUMENTOS[chave] = {"xml": caminho_xml, "pdf": caminho_pdf}
        partes = []
        if caminho_xml is not None:
            partes.append("XML")
        if caminho_pdf is not None:
            partes.append("PDF")
        log("Nota %s: %s salvo." % (nota.get("numero") or chave[-6:], " e ".join(partes)))


def _copiar_documentos_para(destino_xlsx, log, documentos=None, subpasta="") -> None:
    import app.portal as portal

    docs = documentos if documentos is not None else (getattr(portal, "_DOCUMENTOS", None) or {})
    if not docs:
        return
    base = Path(destino_xlsx).parent
    if subpasta:
        base = base / subpasta
    quantos = {"xml": 0, "pdf": 0}
    for item in docs.values():
        for tipo in ("xml", "pdf"):
            origem = item.get(tipo)
            if origem is None:
                continue
            origem = Path(origem)
            if not origem.is_file():
                continue
            pasta = base / tipo
            pasta.mkdir(parents=True, exist_ok=True)
            alvo = pasta / origem.name
            if alvo.resolve() != origem.resolve():
                shutil.copy2(origem, alvo)
            quantos[tipo] += 1
    if quantos["xml"] or quantos["pdf"]:
        log("XML: %s. PDF: %s. Pasta: %s" % (quantos["xml"], quantos["pdf"], base))


def _salvar_planilha_ja(notas, periodo, pasta_dados, log, subpasta=""):
    if not notas:
        return None
    from pathlib import Path

    from app.excel import gravar_planilha, nome_arquivo

    pasta = Path(str(pasta_dados)) / "planilhas" / "rascunhos"
    pasta.mkdir(parents=True, exist_ok=True)
    padrao = nome_arquivo(pasta, mes=periodo.inicio.month, ano=periodo.inicio.year)
    selo = "%s a %s %s" % (
        periodo.inicio.strftime("%d-%m-%Y"),
        periodo.fim.strftime("%d-%m-%Y"),
        datetime.now().strftime("%H%M%S"),
    )
    destino = padrao.with_name("%s %s%s" % (padrao.stem, selo, padrao.suffix))
    gravar_planilha(notas, destino, rotulo_periodo=periodo.rotulo)
    _liberar_planilha(destino)
    log("Cópia de segurança em: " + str(destino))
    try:
        _copiar_documentos_para(destino, log, subpasta=subpasta)
    except Exception as e:
        log("Não consegui copiar XML e PDF agora: " + str(e).split("\n", 1)[0])
    return destino


def _instalar_coleta_resiliente() -> None:
    import app.portal as portal

    if getattr(portal.executar, "_resiliente", False):
        return

    def executar(*, pasta_dados, periodo, parar, confirmar_login, log):
        from playwright.sync_api import sync_playwright

        _instalar_login_direto()
        log("Abrindo o Chrome no Emissor Nacional…")
        try:
            playwright = sync_playwright().start()
        except Exception as e:
            raise RuntimeError(
                "Não consegui abrir o Google Chrome. Confira se o Chrome está instalado "
                "e feche outras janelas que usem o mesmo perfil do Coletor.\nDetalhe: " + str(e)
            ) from e
        vivos = getattr(portal, "_PLAYWRIGHTS", None)
        if vivos is None:
            vivos = []
            portal._PLAYWRIGHTS = vivos
        vivos.append(playwright)
        ctx = None
        grupos = []
        try:
            try:
                ctx = portal.abrir_navegador(playwright, pasta_dados)
            except Exception as e:
                raise RuntimeError(
                    "Não consegui abrir o Google Chrome. Confira se o Chrome está instalado "
                    "e feche outras janelas que usem o mesmo perfil do Coletor.\nDetalhe: " + str(e)
                ) from e
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            portal._forcar_tela_login(page, log)
            aplicar_tipo("prestados")
            page = portal.aguardar_login(page, parar, confirmar_login, log)
            log("A coleta começa pelos serviços prestados (NFS-e Emitidas). Depois seguem os tomados.")
            falha = None
            for tipo, titulo, pasta_nome in (
                ("prestados", "Serviços prestados", "Prestados"),
                ("tomados", "Serviços tomados", "Tomados"),
            ):
                if parar.is_set():
                    break
                aplicar_tipo(tipo)
                _preparar_documentos(pasta_dados, tipo)
                log(titulo + ".")
                notas = []
                try:
                    page = portal._ir_para_recebidas(page, log)
                    for ini, fim in periodo.fatias():
                        if parar.is_set():
                            break
                        page = portal._ir_para_recebidas(page, log)
                        novas = portal._coletar_periodo(page, ini, fim, parar, log)
                        notas.extend(novas)
                        try:
                            _baixar_documentos(page, novas, parar, log)
                        except Exception as falha_arquivo:
                            log(
                                "Não consegui baixar XML e PDF desta parte: "
                                + str(falha_arquivo).split("\n", 1)[0]
                            )
                except InterruptedError:
                    if notas:
                        _salvar_planilha_ja(notas, periodo, pasta_dados, log, pasta_nome)
                    grupos.append(
                        {
                            "tipo": tipo,
                            "titulo": titulo,
                            "pasta": pasta_nome,
                            "notas": notas,
                            "docs": dict(getattr(portal, "_DOCUMENTOS", {}) or {}),
                        }
                    )
                    log("Coleta interrompida.")
                    return grupos
                except Exception as e:
                    falha = e
                    try:
                        portal._salvar_evidencia(page, log, "falha na coleta")
                    except Exception:
                        pass
                    log("%s: %s" % (titulo, str(e).split("\n", 1)[0]))
                if notas:
                    try:
                        _salvar_planilha_ja(notas, periodo, pasta_dados, log, pasta_nome)
                    except Exception as falha_planilha:
                        log("Não consegui gravar a planilha agora: " + str(falha_planilha))
                else:
                    log("%s: nenhuma nota neste período." % titulo)
                grupos.append(
                    {
                        "tipo": tipo,
                        "titulo": titulo,
                        "pasta": pasta_nome,
                        "notas": notas,
                        "docs": dict(getattr(portal, "_DOCUMENTOS", {}) or {}),
                    }
                )
            if parar.is_set():
                log("Coleta interrompida.")
            elif not any(g["notas"] for g in grupos) and falha is not None:
                raise falha
            else:
                total = sum(len(g["notas"]) for g in grupos)
                log("Coleta concluída: %s nota(s), prestados e tomados." % total)
            return grupos
        finally:
            if ctx is not None:
                import threading

                def _fechar():
                    try:
                        ctx.close()
                    except Exception:
                        pass

                threading.Thread(target=_fechar, daemon=True).start()

    executar._resiliente = True
    portal.executar = executar


def _instalar_retomada(parar, confirmar) -> None:
    import app.portal as portal

    estado = getattr(portal, "_RETOMADA", None)
    if estado is None:
        portal._RETOMADA = {"parar": parar, "confirmar": confirmar}
    else:
        estado["parar"] = parar
        estado["confirmar"] = confirmar
        return

    abrir_original = portal._abrir_por_chave
    visualizar_original = portal._abrir_visualizar

    def _abrir_por_chave(page, chave, url_lista, log):
        if _em_login(page):
            try:
                _renovar_sessao(page, url_lista, log)
            except Exception as e:
                log("Não consegui renovar a sessão: " + str(e))
                parar.set()
                return None
        try:
            pagina = abrir_original(page, chave, url_lista, log)
        except Exception:
            pagina = None
        if pagina is not None and not _em_login(pagina) and not _em_login(page):
            return pagina
        if not _em_login(page) and not _em_login(pagina):
            return pagina
        try:
            _renovar_sessao(page, url_lista, log)
        except Exception as e:
            log("Não consegui renovar a sessão: " + str(e))
            parar.set()
            return None
        if _em_login(page):
            log("O login novo não foi concluído. Paro por aqui e salvo as notas já lidas.")
            parar.set()
            return None
        try:
            return abrir_original(page, chave, url_lista, log)
        except Exception:
            return None

    def _abrir_visualizar(page, row, url_lista, log):
        if _em_login(page):
            try:
                _renovar_sessao(page, url_lista, log)
            except Exception as e:
                log("Não consegui renovar a sessão: " + str(e))
                parar.set()
                return None
            if _em_login(page):
                log("O login novo não foi concluído. Paro por aqui e salvo as notas já lidas.")
                parar.set()
                return None
        return visualizar_original(page, row, url_lista, log)

    portal._abrir_por_chave = _abrir_por_chave
    portal._abrir_visualizar = _abrir_visualizar


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_TITLE}  ·  v{__version__}")
        self.geometry("720x760")
        self.minsize(680, 700)
        self.configure(bg=FUNDO)
        self._mutex_handle = None
        self._parar = threading.Event()
        self._confirmar_login = threading.Event()
        self._trabalhando = False
        self._fotos: list = []
        self._configurar_janela_windows()
        self._aplicar_icone()
        self._montar()

    def _configurar_janela_windows(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
            ctypes.windll.kernel32.SetLastError(0)
            self._mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
        except Exception:
            pass

    def _recurso(self, *nomes: str):
        bases = [raiz_app() / "recursos", Path(__file__).resolve().parent]
        for nome in nomes:
            for base in bases:
                caminho = base / nome
                if caminho.is_file():
                    return caminho
        return None

    def _aplicar_icone(self) -> None:
        ico = self._recurso("coletor.ico")
        if not ico:
            return
        try:
            self.iconbitmap(str(ico))
        except Exception:
            pass

    def _montar(self) -> None:
        topo = tk.Frame(self, bg=FUNDO)
        topo.pack(fill="x", padx=18, pady=(14, 2))
        ico_png = self._recurso("coletor-icone-hero.png", "coletor-icone-difal.png")
        if ico_png is not None:
            try:
                foto = tk.PhotoImage(file=str(ico_png))
                w = max(foto.width(), 1)
                h = max(foto.height(), 1)
                alvo = 96
                if w > alvo or h > alvo:
                    fator = max(1, max(w, h) // alvo)
                    foto = foto.subsample(fator, fator)
                self._fotos.append(foto)
                tk.Label(topo, image=foto, bg=FUNDO).pack(side="left", padx=(0, 12))
            except Exception:
                pass
        textos = tk.Frame(topo, bg=FUNDO)
        textos.pack(side="left", fill="x", expand=True)
        tk.Label(
            textos,
            text="Apanhador de NFS-e",
            bg=FUNDO,
            fg=AZUL,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")
        tk.Label(
            textos,
            text="Informe o período. A coleta começa pelos serviços prestados e depois busca os tomados, com relatório, XML e PDF. No Chrome, entre com senha ou certificado: o robô abre a primeira tela sozinho.",
            bg=FUNDO,
            fg=TEXTO,
            font=("Segoe UI", 10),
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        box = tk.Frame(self, bg="white", highlightbackground="#dbe3f0", highlightthickness=1)
        box.pack(fill="x", padx=18, pady=12)

        self.modo = tk.StringVar(value="mes")
        tk.Radiobutton(
            box,
            text="Mês e ano (do dia 1 ao último dia)",
            variable=self.modo,
            value="mes",
            command=self._alternar_modo,
            bg="white",
            fg=TEXTO,
            activebackground="white",
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 2))

        tk.Label(box, text="Mês", bg="white", fg=TEXTO).grid(row=1, column=0, sticky="w", padx=28)
        self.cmb_mes = ttk.Combobox(box, values=MESES, state="readonly", width=18)
        agora = datetime.now()
        self.cmb_mes.set(MESES[agora.month - 1])
        self.cmb_mes.grid(row=1, column=1, sticky="w", pady=2)
        tk.Label(box, text="Ano", bg="white", fg=TEXTO).grid(row=2, column=0, sticky="w", padx=28)
        self.var_ano = tk.StringVar(value=str(agora.year))
        self.spn_ano = tk.Spinbox(box, from_=2020, to=2100, textvariable=self.var_ano, width=8)
        self.spn_ano.grid(row=2, column=1, sticky="w", pady=(0, 6))

        tk.Radiobutton(
            box,
            text="Período livre (data inicial e data final)",
            variable=self.modo,
            value="periodo",
            command=self._alternar_modo,
            bg="white",
            fg=TEXTO,
            activebackground="white",
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2))
        tk.Label(box, text="Data inicial", bg="white", fg=TEXTO).grid(row=4, column=0, sticky="w", padx=28)
        self.var_ini = tk.StringVar(value=f"01/{agora.month:02d}/{agora.year}")
        self.ent_ini = tk.Entry(box, textvariable=self.var_ini, width=16)
        self.ent_ini.grid(row=4, column=1, sticky="w", pady=2)
        tk.Label(box, text="Data final", bg="white", fg=TEXTO).grid(row=5, column=0, sticky="w", padx=28)
        self.var_fim = tk.StringVar(value=agora.strftime("%d/%m/%Y"))
        self.ent_fim = tk.Entry(box, textvariable=self.var_fim, width=16)
        self.ent_fim.grid(row=5, column=1, sticky="w", pady=(0, 4))
        tk.Label(
            box,
            text="Formato: dd/mm/aaaa.",
            bg="white",
            fg="#64748b",
            font=("Segoe UI", 9),
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 12))

        acoes = tk.Frame(self, bg=FUNDO)
        acoes.pack(fill="x", padx=18)
        self.btn_iniciar = tk.Button(
            acoes,
            text="Iniciar robô",
            command=self._iniciar,
            bg=VERDE,
            fg="white",
            activebackground="#115e59",
            activeforeground="white",
            relief="flat",
            padx=16,
            pady=8,
            font=("Segoe UI", 10, "bold"),
        )
        self.btn_iniciar.pack(side="left")
        self.btn_parar = tk.Button(
            acoes,
            text="Parar",
            command=self._pedir_parar,
            state="disabled",
            relief="flat",
            padx=16,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.btn_parar.pack(side="left", padx=8)

        self.painel_salvar = tk.Frame(self, bg="#ecfdf5", highlightbackground=VERDE, highlightthickness=1)
        self.lbl_salvar = tk.Label(
            self.painel_salvar,
            text="",
            bg="#ecfdf5",
            fg="#115e59",
            font=("Segoe UI", 11, "bold"),
            justify="left",
            anchor="w",
        )
        self.lbl_salvar.pack(fill="x", padx=12, pady=(10, 4))
        self.var_caminho = tk.StringVar()
        ent_caminho = tk.Entry(
            self.painel_salvar,
            textvariable=self.var_caminho,
            font=("Segoe UI", 10),
            readonlybackground="white",
            relief="solid",
            borderwidth=1,
        )
        ent_caminho.configure(state="readonly")
        ent_caminho.pack(fill="x", padx=12, pady=(0, 8))
        botoes_salvar = tk.Frame(self.painel_salvar, bg="#ecfdf5")
        botoes_salvar.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(
            botoes_salvar,
            text="Abrir pasta",
            command=self._abrir_planilha,
            bg=VERDE,
            fg="white",
            activebackground="#115e59",
            activeforeground="white",
            relief="flat",
            padx=12,
            pady=6,
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")
        tk.Button(
            botoes_salvar,
            text="Salvar em outro lugar",
            command=self._salvar_em_outro_lugar,
            relief="flat",
            padx=12,
            pady=6,
            font=("Segoe UI", 10),
        ).pack(side="left", padx=8)

        self.log = tk.Text(self, height=12, wrap="word", font=("Consolas", 9), bg="white", fg=TEXTO)
        self.log.pack(fill="both", expand=True, padx=18, pady=(10, 4))
        self.log.configure(state="disabled")
        self.var_status = tk.StringVar(value=f"v{__version__}")
        tk.Label(self, textvariable=self.var_status, bg=FUNDO, fg="#64748b", anchor="w").pack(
            fill="x", padx=18, pady=(0, 10)
        )
        self._alternar_modo()

    def _alternar_modo(self) -> None:
        mes = self.modo.get() == "mes"
        estado_mes = "readonly" if mes else "disabled"
        estado_ano = "normal" if mes else "disabled"
        estado_periodo = "normal" if not mes else "disabled"
        self.cmb_mes.configure(state=estado_mes)
        self.spn_ano.configure(state=estado_ano)
        self.ent_ini.configure(state=estado_periodo)
        self.ent_fim.configure(state=estado_periodo)

    def _escrever(self, msg: str) -> None:
        if "30 dias" in msg or msg.startswith("Parte ") or msg.startswith("Nenhuma nota neste período"):
            return
        linha = datetime.now().strftime("%H:%M:%S") + "  " + msg
        try:
            pasta = pasta_dados()
            pasta.mkdir(parents=True, exist_ok=True)
            with (pasta / "execucao.log").open("a", encoding="utf-8") as arquivo:
                arquivo.write(linha + "\n")
        except Exception:
            pass

        def _ok() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", linha + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
            self.var_status.set(msg[:180])

        self.after(0, _ok)

    def _pasta_sugerida(self) -> Path:
        home = Path.home()
        for nome in ("Documentos", "Documents"):
            pasta = home / nome
            if pasta.is_dir():
                return pasta
        return home

    def _lembrar_destino(self, caminho: Path) -> None:
        try:
            (pasta_dados() / "ultimo_destino.txt").write_text(str(caminho), encoding="utf-8")
        except Exception:
            pass

    def _pedir_destino(self, titulo, nome_sugerido):
        base = self._pasta_sugerida()
        try:
            self.attributes("-topmost", False)
            self.update_idletasks()
        except Exception:
            pass
        escolhido = filedialog.asksaveasfilename(
            parent=self,
            title=titulo,
            initialdir=str(base),
            initialfile=nome_sugerido,
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
        )
        if not escolhido:
            return None
        destino = Path(escolhido)
        if destino.suffix.lower() != ".xlsx":
            destino = destino.with_suffix(".xlsx")
        return destino

    def _escolher_destino_novo(self, grupo, periodo):
        aplicar_tipo(grupo["tipo"])
        sugerido = nome_arquivo(
            self._pasta_sugerida(),
            mes=periodo.inicio.month,
            ano=periodo.inicio.year,
        ).name
        titulo = "Onde salvar o relatório de %s?" % grupo["titulo"].lower()
        while True:
            destino = self._pedir_destino(titulo, sugerido)
            if destino is None:
                return None
            if destino.exists():
                messagebox.showwarning(
                    "Coletor",
                    "Esse arquivo já existe:\n%s\n\nEscolha outro nome ou outra pasta. O Coletor não substitui uma planilha já salva."
                    % destino,
                )
                sugerido = destino.name
                continue
            return destino

    def _periodo(self):
        if self.modo.get() == "mes":
            ano = int(self.var_ano.get().strip())
            mes = MESES.index(self.cmb_mes.get()) + 1
            return periodo_do_mes(mes, ano)
        return periodo_livre(self.var_ini.get(), self.var_fim.get())

    def _iniciar(self) -> None:
        if self._trabalhando:
            return
        try:
            periodo = self._periodo()
        except Exception as e:
            messagebox.showerror("Período inválido", str(e))
            return
        self._trabalhando = True
        self._parar.clear()
        self.painel_salvar.pack_forget()
        self.btn_iniciar.configure(state="disabled")
        self.btn_parar.configure(state="normal")
        self._escrever(
            f"Período: {periodo.rotulo} ({periodo.inicio.strftime('%d/%m/%Y')} a {periodo.fim.strftime('%d/%m/%Y')}). Coleta de serviços prestados e serviços tomados."
        )
        if self.modo.get() == "mes":
            mes_ano = (MESES.index(self.cmb_mes.get()) + 1, int(self.var_ano.get().strip()))
        else:
            mes_ano = None
        threading.Thread(target=self._trabalhar, args=(periodo, mes_ano), daemon=True).start()

    def _pedir_parar(self) -> None:
        self._parar.set()
        self._escrever("Parando… aguarde a etapa atual.")

    def _trabalhar(self, periodo, mes_ano) -> None:
        _instalar_retomada(self._parar, self._confirmar_login)
        _instalar_coleta_resiliente()
        from app.portal import executar

        grupos = []
        erro = None
        cancelado = False
        try:
            grupos = executar(
                pasta_dados=pasta_dados(),
                periodo=periodo,
                parar=self._parar,
                confirmar_login=self._confirmar_login,
                log=self._escrever,
            ) or []
            cancelado = self._parar.is_set()
            tem = any(g.get("notas") for g in grupos)
            if cancelado and not tem:
                self._escrever("Coleta cancelada.")
            elif cancelado and tem:
                self._escrever("Coleta parada. Vou guardar o que já tinha sido lido.")
        except InterruptedError as e:
            self._escrever(str(e))
            cancelado = True
        except Exception as e:
            erro = str(e)
            tb = traceback.format_exc()
            self._escrever("Erro: " + erro)
            self._escrever(tb)
            try:
                (pasta_dados() / "ultimo_erro.txt").write_text(erro + "\n\n" + tb, encoding="utf-8")
            except Exception:
                pass
        self.after(0, lambda g=grupos: self._terminou(g, periodo, mes_ano, erro, cancelado))

    def _gravar_grupo(self, grupo, destino, periodo):
        aplicar_tipo(grupo["tipo"])
        destino = Path(destino)
        destino.parent.mkdir(parents=True, exist_ok=True)
        gravar_planilha(grupo["notas"], destino, rotulo_periodo=periodo.rotulo)
        _liberar_planilha(destino)
        _copiar_documentos_para(
            destino,
            self._escrever,
            documentos=grupo.get("docs"),
            subpasta=grupo["pasta"],
        )
        return destino

    def _terminou(self, grupos, periodo, mes_ano, erro, cancelado) -> None:
        self._trabalhando = False
        self.btn_iniciar.configure(state="normal")
        self.btn_parar.configure(state="disabled")
        grupos = list(grupos or [])
        com_nota = [g for g in grupos if g.get("notas")]
        if erro and com_nota:
            if not messagebox.askyesno(
                "Coletor",
                "Deu erro, mas já tinha nota lida. Quer salvar o Excel mesmo assim?",
            ):
                messagebox.showerror("Coletor", erro)
                return
        elif erro:
            messagebox.showerror("Coletor", erro)
            return
        if not com_nota:
            messagebox.showinfo("Coletor", "Nenhuma nota neste período. Não há Excel para salvar.")
            return
        self._grupos_prontos = com_nota
        self._periodo_pronto = periodo
        escritos = []
        try:
            for grupo in com_nota:
                destino = self._escolher_destino_novo(grupo, periodo)
                if destino is None:
                    self._escrever(
                        "Você fechou a janela de salvar %s. A cópia de segurança continua em dados\\planilhas\\rascunhos."
                        % grupo["titulo"].lower()
                    )
                    continue
                destino = self._gravar_grupo(grupo, destino, periodo)
                escritos.append(destino)
                self._escrever("Planilha pronta em: " + str(destino))
        except Exception as e:
            messagebox.showerror("Coletor", "Não consegui gravar o Excel:\n" + str(e))
            return
        if not escritos:
            messagebox.showinfo(
                "Coletor",
                "Nenhuma planilha foi salva na pasta que você escolhe. A cópia de segurança ficou em dados\\planilhas\\rascunhos.",
            )
            return
        self._lembrar_destino(escritos[0])
        self._planilha_pronta = escritos[0].parent
        total = sum(len(g["notas"]) for g in com_nota)
        self._mostrar_planilha(self._planilha_pronta, total)

    def _mostrar_planilha(self, destino, quantidade) -> None:
        self.lbl_salvar.configure(
            text="Prestados e tomados prontos, %s notas. Relatórios, XML e PDF estão na pasta:" % quantidade
        )
        self.var_caminho.set(str(destino))
        if not self.painel_salvar.winfo_ismapped():
            self.painel_salvar.pack(fill="x", padx=18, pady=(8, 0), before=self.log)
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _abrir_planilha(self) -> None:
        destino = getattr(self, "_planilha_pronta", None)
        if destino is None:
            return
        try:
            os.startfile(destino)
        except Exception as e:
            messagebox.showwarning("Coletor", "Não consegui abrir a pasta: " + str(e))

    def _salvar_em_outro_lugar(self) -> None:
        grupos = getattr(self, "_grupos_prontos", None) or []
        periodo = getattr(self, "_periodo_pronto", None)
        if not grupos or periodo is None:
            return
        try:
            self.attributes("-topmost", False)
        except Exception:
            pass
        escolhida = filedialog.askdirectory(
            parent=self,
            title="Pasta para os relatórios, XML e PDF",
            initialdir=str(self._pasta_sugerida()),
        )
        if not escolhida:
            return
        pasta = Path(escolhida)
        try:
            primeiro = None
            for grupo in grupos:
                aplicar_tipo(grupo["tipo"])
                nome = nome_arquivo(pasta, mes=periodo.inicio.month, ano=periodo.inicio.year)
                if nome.exists():
                    messagebox.showwarning(
                        "Coletor",
                        "Esse arquivo já existe:\n%s\n\nNão vou substituir." % nome,
                    )
                    continue
                destino = self._gravar_grupo(grupo, nome, periodo)
                if primeiro is None:
                    primeiro = destino
                self._escrever("Planilha também salva em: " + str(destino))
        except Exception as e:
            messagebox.showerror("Coletor", "Não consegui gravar o Excel:\n" + str(e))
            return
        if primeiro is not None:
            self._lembrar_destino(primeiro)
            self._planilha_pronta = primeiro.parent
            self.var_caminho.set(str(primeiro.parent))


def run() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run()
