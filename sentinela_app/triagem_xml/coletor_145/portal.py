from __future__ import annotations

import random
import re
import time
import unicodedata
from collections.abc import Callable
from datetime import date
from pathlib import Path
from threading import Event

from playwright.sync_api import Locator, Page, Playwright, TimeoutError as PlaywrightTimeout

from app.periodo import Periodo, fmt_br

URL_LOGIN = "https://www.nfse.gov.br/EmissorNacional/Login?ReturnUrl=%2femissornacional"
URL_DASHBOARD = "https://www.nfse.gov.br/EmissorNacional/Dashboard"
URL_RECEBIDAS = "https://www.nfse.gov.br/EmissorNacional/Notas/Recebidas"

LogFn = Callable[[str], None]
PAUSA_ENTRE_NOTAS_MIN_MS = 8_000
PAUSA_ENTRE_NOTAS_MAX_MS = 15_000

RE_RECEBIDAS = re.compile(r"NFS-e\s+Recebidas", re.I)
RE_VISUALIZAR = re.compile(r"^\s*Visualizar", re.I)
RE_FILTRAR = re.compile(r"^\s*Filtrar", re.I)
RE_LOGIN = re.compile(r"/login|acesso\.gov\.br|sso\.acesso|gov\.br/login", re.I)
RE_SOBRECARGA = re.compile(
    r"consumo|sobrecarga|alto volume|tente novamente|indispon[ií]vel|"
    r"temporariamente|too many|aguarde alguns|sistema (está )?ocupado",
    re.I,
)

_JS_EXTRAIR = r"""
() => {
  const limpar = (s) => (s || "").replace(/\s+/g, " ").trim();

  const textoEl = (el) => {
    if (!el) return "";
    const v = (el.value || "").trim();
    if (v) return limpar(v);
    return limpar(el.innerText || el.textContent || "");
  };

  const rotuloDe = (el) => limpar(el.innerText || el.textContent || "")
    .toLowerCase().replace(/[:*]\s*$/, "");

  function pareceDinheiro(v) {
    const t = limpar(v);
    if (!t) return false;
    if (/descri[cç][aã]o/i.test(t) && t.length > 24) return false;
    if (/r\$\s*-?[\d.]*\d,\d{2}/i.test(t)) return true;
    if (/^-?\d{1,3}(\.\d{3})*,\d{2}$/.test(t)) return true;
    if (/^-?\d+,\d{2}$/.test(t)) return true;
    return false;
  }

  function valorNoPainel(tituloPainel, rotulos, exigirDinheiro) {
    const paineis = [...document.querySelectorAll(".panel, .card, fieldset, section, .box")];
    const alvos = tituloPainel
      ? paineis.filter((p) => {
          const t = p.querySelector(".panel-title, .card-header, h3, h4, legend, .titulo");
          const txt = t ? limpar(t.innerText || t.textContent || "").toLowerCase() : "";
          return txt.includes(tituloPainel.toLowerCase());
        })
      : [];
    const buscarEm = alvos.length ? alvos : [document];
    for (const raiz of buscarEm) {
      const grupos = [...raiz.querySelectorAll(".form-group, .campo, .row > div")];
      for (const rotulo of rotulos) {
        const r = rotulo.toLowerCase();
        for (const g of grupos) {
          const primeira = limpar((g.innerText || "").split("\n")[0]).toLowerCase().replace(/[:*]\s*$/, "");
          const lab = g.querySelector("label, .control-label, small");
          const tl = lab ? rotuloDe(lab) : primeira;
          if (/descri[cç][aã]o/.test(tl) || /descri[cç][aã]o/.test(primeira)) continue;
          if (tl !== r && !tl.startsWith(r) && primeira !== r && !primeira.startsWith(r)) continue;
          if (tl.length > 80 && primeira.length > 80) continue;
          const campo = g.querySelector(
            "input:not([type=hidden]), textarea, select, span.texto, span.numero, .form-control"
          );
          const candidatos = [textoEl(campo)];
          const linhas = limpar(g.innerText || "").split("\n").map((x) => x.trim()).filter(Boolean);
          if (linhas.length >= 2) candidatos.push(linhas[1]);
          const escolhido = exigirDinheiro
            ? candidatos.find(pareceDinheiro)
            : candidatos.find((v) => v && v.toLowerCase() !== r);
          if (escolhido) return escolhido;
        }
      }
    }
    return "";
  }

  function valorGlobal(rotulos, exigirDinheiro) {
    return valorNoPainel("", rotulos, exigirDinheiro);
  }

  const numero =
    valorNoPainel("Identificação da NFS-e", ["Número da NFS-e", "Número NFS-e", "Número", "Nº"]) ||
    valorNoPainel("Identificação do DPS", ["Número", "Número da DPS", "Nº"]) ||
    valorGlobal(["Número da NFS-e", "Número NFS-e"]);

  const competenciaEmissao =
    valorNoPainel("Identificação da NFS-e", ["Competência", "Competencia"]) ||
    valorNoPainel("Identificação do DPS", ["Competência", "Competencia"]) ||
    valorGlobal(["Competência", "Competencia"]) ||
    (() => {
      const txt = (document.body && document.body.innerText) || "";
      const m = txt.match(/(?:Compet[eê]ncia|Competencia)[^\d]{0,80}(\d{2}\/\d{4})/i);
      return m ? limpar(m[1]) : "";
    })();

  const data =
    valorNoPainel("Identificação do DPS", ["Data de emissão", "Data Emissão", "Data de emissao"]) ||
    valorNoPainel("Identificação da NFS-e", ["Data de geração", "Data de geracao", "Data Emissão", "Data de emissão"]) ||
    valorGlobal(["Data de emissão", "Data Emissão", "Data de geração"]);

  function ehTituloCancelamento(txt) {
    const t = limpar(txt);
    if (!t) return false;
    if (/solicita[cç][aã]o\s+de\s+an[aá]lise/i.test(t)) return false;
    if (/indeferido/i.test(t)) return false;
    if (/evento\s+de\s+cancelamento/i.test(t)) return true;
    if (/cancelamento\s+de\s+nfs-?e\s+(deferido|por\s+of[ií]cio)/i.test(t)) return true;
    return false;
  }

  function dataRegistroNoTexto(txt) {
    const m = (txt || "").match(
      /Data do registro do Evento[:\s]*(\d{2}\/\d{2}\/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)/i
    );
    return m ? limpar(m[1]) : "";
  }

  function extrairCancelamento() {
    const seletoresPainel = ".panel, .card, fieldset, section, .box, .evento, [class*='evento']";
    let painel = null;
    for (const p of document.querySelectorAll(seletoresPainel)) {
      const t = p.querySelector(".panel-title, .card-header, h3, h4, h5, legend, .titulo, .panel-heading");
      const cab = t ? (t.innerText || t.textContent || "") : ((p.innerText || "").split("\n")[0] || "");
      if (ehTituloCancelamento(cab)) { painel = p; break; }
    }
    if (!painel) {
      const hit = [...document.querySelectorAll("h1,h2,h3,h4,h5,legend,.panel-title,.card-header,strong")].find(
        (n) => ehTituloCancelamento(n.innerText || n.textContent || "")
      );
      if (hit) painel = hit.closest(seletoresPainel) || hit.parentElement;
    }

    let dataCancel = "";
    if (painel) {
      dataCancel = dataRegistroNoTexto(painel.innerText || "");
      if (!dataCancel) {
        dataCancel = valorNoPainel("Evento de Cancelamento", [
          "Data do registro do Evento",
          "Data do registro do evento",
          "Data de registro do Evento",
        ]);
      }
    }
    if (!dataCancel) {
      const bloco = (document.body.innerText || "").match(
        /Evento de Cancelamento[\s\S]{0,1200}?Data do registro do Evento[:\s]*(\d{2}\/\d{2}\/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)/i
      );
      if (bloco) dataCancel = limpar(bloco[1]);
    }
    if (!dataCancel) {
      for (const tb of document.querySelectorAll("table")) {
        const ths = [...tb.querySelectorAll("th, thead td")].map((el) => limpar(el.innerText || "").toLowerCase());
        const idxData = ths.findIndex((t) => /data do registro/.test(t) || t === "data");
        for (const tr of tb.querySelectorAll("tbody tr, tr")) {
          const tds = [...tr.querySelectorAll("td")].map((td) => limpar(td.innerText || ""));
          const linha = tds.join(" | ");
          if (!ehTituloCancelamento(linha)) continue;
          if (idxData >= 0 && tds[idxData]) { dataCancel = tds[idxData]; break; }
          const md = linha.match(/(\d{2}\/\d{2}\/\d{4}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)/);
          if (md) { dataCancel = md[1]; break; }
        }
        if (dataCancel) break;
      }
    }

    const cancelada = !!(painel || dataCancel);
    return {
      status: cancelada ? "Cancelada" : "Ativa",
      data_cancelamento: cancelada ? dataCancel : "",
    };
  }

  const cancelamento = extrairCancelamento();

  return {
    numero,
    data_emissao: data,
    competencia_emissao: competenciaEmissao,
    razao_emitente: valorNoPainel("Emitente", ["Razão Social", "Razao Social"]),
    valor_servico: valorNoPainel("Tributação Municipal", ["Valor do Serviço", "Valor do Servico", "Valor dos Serviços"], true),
    valor_liquido: (() => {
      const rotulos = [
        "Valor Líquido da NFS-e",
        "Valor Liquido da NFS-e",
        "Valor Líquido da Nota",
        "Valor Liquido da Nota",
        "Valor Líquido",
        "Valor Liquido",
        "Total Líquido",
      ];
      let v =
        valorNoPainel("Valores", rotulos, true) ||
        valorNoPainel("Dados do Valor Total", rotulos, true) ||
        valorNoPainel("Valor Total", rotulos, true) ||
        valorNoPainel("Tributação Municipal", rotulos, true) ||
        valorGlobal(rotulos, true);
      if (v) return v;
      const txt = (document.body && document.body.innerText) || "";
      const padroes = [
        /Valor\s+L[ií]quido(?:\s+da\s+NFS-?e)?(?:\s*\+\s*IBS\/CBS)?[^\dR$]{0,80}R?\$?\s*([\d]{1,3}(?:\.[\d]{3})*,\d{2}|\d+,\d{2})/i,
        /Valor\s+Liquido(?:\s+da\s+NFS-?e)?(?:\s*\+\s*IBS\/CBS)?[^\dR$]{0,80}R?\$?\s*([\d]{1,3}(?:\.[\d]{3})*,\d{2}|\d+,\d{2})/i,
      ];
      for (const re of padroes) {
        const m = txt.match(re);
        if (m && m[1]) return limpar(m[1]);
      }
      return "";
    })(),
    retencao: valorNoPainel("Tributação Municipal", ["Retenção", "Retencao"]),
    valor_issqn: valorNoPainel("Tributação Municipal", ["Valor do ISSQN", "Valor ISSQN"], true),
    status: cancelamento.status,
    data_cancelamento: cancelamento.data_cancelamento,
  };
}
"""

_JS_EXTRAIR_OUTROS = r"""
() => {
  const limpar = (s) => (s || "").replace(/\s+/g, " ").trim();
  const norm = (s) => limpar(s).toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[:*]/g, " ").replace(/[-–—]/g, " ").replace(/\s+/g, " ").trim();

  const textoEl = (el) => {
    if (!el) return "";
    const v = (el.value || "").trim();
    if (v) return limpar(v);
    return limpar(el.innerText || el.textContent || "");
  };

  function raizBusca() {
    const ativo = document.querySelector(".tab-pane.active, .tab-content > .active");
    if (ativo && limpar(ativo.innerText || "").length > 20) return ativo;
    for (const id of ["tributacao", "outrostributos", "outros-tributos", "outrosTributos", "tributos"]) {
      const el = document.getElementById(id);
      if (el) return el;
    }
    return document;
  }

  function pareceDinheiro(v) {
    const t = limpar(v);
    if (!t) return false;
    if (/^descricao\b/i.test(norm(t))) return false;
    if (/r\$\s*-?[\d.]*\d,\d{2}/i.test(t)) return true;
    if (/^-?\d{1,3}(\.\d{3})*,\d{2}$/.test(t)) return true;
    if (/^-?\d+,\d{2}$/.test(t)) return true;
    return false;
  }

  function valorPorRotulos(candidatos) {
    const raiz = raizBusca();
    const alvos = candidatos.map(norm);
    const grupos = [...raiz.querySelectorAll(".form-group, .campo, .row > div")];
    let melhor = { score: 99, valor: "" };
    for (const g of grupos) {
      const lab = g.querySelector("label, .control-label, small");
      const bloco = limpar(g.innerText || "");
      const primeira = norm(bloco.split("\n")[0] || "");
      const tl = norm(lab ? (lab.innerText || lab.textContent || "") : primeira);
      if (tl.length > 90) continue;
      if (/\bdescricao\b/.test(tl) || /\bdescricao\b/.test(primeira)) continue;
      let score = 99;
      for (const a of alvos) {
        if (!a) continue;
        if (tl === a || primeira === a) score = Math.min(score, 0);
        else if (tl.startsWith(a) || primeira.startsWith(a)) score = Math.min(score, 1);
      }
      if (score === 99) continue;
      const campo = g.querySelector(
        "input:not([type=hidden]), textarea, select, span.numero, span.texto, .form-control"
      );
      const candidatosValor = [textoEl(campo)];
      const linhas = bloco.split("\n").map((x) => x.trim()).filter(Boolean);
      if (linhas.length >= 2) candidatosValor.push(linhas[1]);
      const escolhido = candidatosValor.find(pareceDinheiro) || "";
      if (!escolhido) continue;
      if (score < melhor.score) melhor = { score, valor: escolhido };
    }
    return melhor.valor;
  }

  return {
    contrib_sociais_retidas: valorPorRotulos([
      "Contribuições Sociais - Retidas",
      "Contribuicoes Sociais - Retidas",
      "Contribuições Sociais Retidas",
      "Contribuicoes Sociais Retidas",
    ]),
    contrib_previdenciaria_retida: valorPorRotulos([
      "Contribuição Previdenciária - Retida",
      "Contribuicao Previdenciaria - Retida",
      "Contribuição Previdenciária Retida",
      "Contribuicao Previdenciaria Retida",
      "Valor Retido CP",
    ]),
    irrf: valorPorRotulos([
      "IRRF",
      "Valor Retido IRRF",
      "IRRF - Valor Retido",
      "Imposto de Renda Retido na Fonte",
    ]),
  };
}
"""

_JS_EXTRAIR_SERVICO = r"""
() => {
  const limpar = (s) => (s || "").replace(/\s+/g, " ").trim();
  const norm = (s) => limpar(s).toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[:*]/g, " ").replace(/[-–—]/g, " ").replace(/\s+/g, " ").trim();

  const textoEl = (el) => {
    if (!el) return "";
    const v = (el.value || "").trim();
    if (v) return limpar(v);
    return limpar(el.innerText || el.textContent || "");
  };

  function raizBusca() {
    const ativo = document.querySelector(".tab-pane.active, .tab-content > .active");
    if (ativo && limpar(ativo.innerText || "").length > 20) return ativo;
    for (const id of ["servico", "serviço", "Servico", "Serviço"]) {
      const el = document.getElementById(id);
      if (el) return el;
    }
    return document;
  }

  function valorPorRotulos(candidatos) {
    const raiz = raizBusca();
    const alvos = candidatos.map(norm);
    const grupos = [...raiz.querySelectorAll(".form-group, .campo, .row > div, dl, tr")];
    for (const g of grupos) {
      const lab = g.querySelector("label, .control-label, small, dt, th");
      const bloco = limpar(g.innerText || "");
      const primeira = norm(bloco.split("\n")[0] || "");
      const tl = norm(lab ? (lab.innerText || lab.textContent || "") : primeira);
      if (tl.length > 90) continue;
      for (let i = 0; i < alvos.length; i++) {
        const alvo = alvos[i];
        if (tl !== alvo && !tl.startsWith(alvo) && primeira !== alvo && !primeira.startsWith(alvo)) continue;
        const campo = g.querySelector(
          "input:not([type=hidden]), textarea, select, span.texto, span.numero, .form-control, dd"
        );
        const candidatosVal = [textoEl(campo)];
        const linhas = bloco.split("\n").map((x) => limpar(x)).filter(Boolean);
        if (linhas.length >= 2) candidatosVal.push(linhas.slice(1).join(" "));
        for (const v of candidatosVal) {
          const val = limpar(v);
          if (!val) continue;
          if (norm(val) === alvo) continue;
          if (/^\d{6}\s*-\s*.+/i.test(val)) return val;
          if (val.length > 8 && !/^c[oó]digo/i.test(val)) return val;
        }
      }
    }
    const txt = limpar(raiz.innerText || "");
    const m = txt.match(
      /C[oó]digo de Tributa[cç][aã]o Nacional[^\d]{0,60}(\d{6}\s*-\s*.+?)(?=\n(?:C[oó]digo|Discrimina|Valor|Descri|$))/i
    );
    if (m && m[1]) return limpar(m[1]);
    return "";
  }

  return {
    codigo_tributacao_nacional: valorPorRotulos([
      "Código de Tributação Nacional",
      "Codigo de Tributacao Nacional",
      "Código Tributação Nacional",
    ]),
  };
}
"""


def perfil_chrome(pasta_dados: Path) -> Path:
    p = pasta_dados / "chrome_perfil"
    p.mkdir(parents=True, exist_ok=True)
    return p


def abrir_navegador(playwright: Playwright, pasta_dados: Path):
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(perfil_chrome(pasta_dados)),
        channel="chrome",
        headless=False,
        slow_mo=45,
        viewport=None,
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--start-maximized",
        ],
        ignore_default_args=["--enable-automation"],
    )


def _pausa_humana(page: Page, minimo_ms: int = 800, maximo_ms: int = 1800) -> None:
    if maximo_ms < minimo_ms:
        maximo_ms = minimo_ms
    page.wait_for_timeout(random.randint(minimo_ms, maximo_ms))


def _pausa_entre_notas(page: Page, log: LogFn) -> None:
    espera_ms = random.randint(PAUSA_ENTRE_NOTAS_MIN_MS, PAUSA_ENTRE_NOTAS_MAX_MS)
    log(f"  Aguardando {espera_ms / 1000:.1f}s antes da próxima nota…")
    page.wait_for_timeout(espera_ms)


def _visivel(loc: Locator) -> bool:
    try:
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def _clicar(loc: Locator, *, timeout: int = 8_000) -> bool:
    el = loc.first
    try:
        el.scroll_into_view_if_needed(timeout=min(timeout, 3_000))
    except Exception:
        pass
    try:
        el.hover(timeout=min(timeout, 3_000))
        el.page.wait_for_timeout(random.randint(120, 420))
    except Exception:
        pass
    try:
        el.click(timeout=timeout, delay=random.randint(50, 140))
        return True
    except Exception:
        try:
            el.click(timeout=timeout, force=True, delay=80)
            return True
        except Exception:
            return False


def _url_norm(page: Page) -> str:
    try:
        return (page.url or "").lower()
    except Exception:
        return ""


def _na_tela_login(page: Page) -> bool:
    url = _url_norm(page)
    if "/dashboard" in url or "/notas/" in url:
        return False
    if "/login" in url or "acesso.gov.br" in url:
        return True
    if "nfse.gov.br" in url and "/emissornacional" in url and "/login" not in url:
        if any(p in url for p in ("/dashboard", "/notas", "/dps", "/configur")):
            return False
    try:
        if "/emissornacional" in url and "/login" not in url:
            return False
        if page.get_by_text(re.compile(r"Acesso com Usuário/Senha|Acesso via GOVBR|Acesso com Certificado", re.I)).count():
            return True
    except Exception:
        pass
    return False


def _logado(page: Page) -> bool:
    url = _url_norm(page)
    if "nfse.gov.br" not in url:
        return False
    if "/login" in url:
        return False
    if "/dashboard" in url or "/notas/" in url:
        return True
    if "/emissornacional" in url:
        return True
    return False


def _href_real(page: Page) -> str:
    try:
        href = page.evaluate("() => String(location.href || '')") or ""
        return str(href).lower()
    except Exception:
        return _url_norm(page)


def _url_da_pagina(page: Page) -> str:
    u = _url_norm(page)
    if u.startswith("http") and "about:blank" not in u:
        return u
    return _href_real(page)


def _portal_pesado(page: Page) -> bool:
    try:
        titulo = (page.title() or "")
        texto = page.locator("h1, h2, .alert, .erro, .error, #conteudo").first.inner_text(timeout=800)
        return bool(RE_SOBRECARGA.search(titulo + " " + texto))
    except Exception:
        return False


def _esperar_se_pesado(page: Page, log: LogFn) -> None:
    if not _portal_pesado(page):
        return
    log("O portal acusou sobrecarga/consumo alto. Esperando 15 segundos para não piorar.")
    page.wait_for_timeout(15_000)


def _pagina_do_painel(context, fallback: Page) -> Page:
    for p in list(context.pages):
        try:
            if p.is_closed():
                continue
            u = _url_da_pagina(p)
            if "/login" in u and "/dashboard" not in u and "/notas/" not in u:
                continue
            if "/dashboard" in u or "/notas/" in u:
                return p
            if "nfse.gov.br" in u and "/emissornacional" in u and "/login" not in u:
                return p
        except Exception:
            continue
    return fallback


def aguardar_login(page: Page, parar: Event, confirmar: Event, log: LogFn, timeout_s: int = 1200) -> Page:
    log("Faça o login no Chrome (senha ou certificado).")
    log("Quando o painel abrir, o robô continua sozinho — não precisa clicar em nada aqui.")
    inicio = time.time()
    visto = 0
    while time.time() - inicio < timeout_s:
        if parar.is_set():
            raise InterruptedError("Coleta interrompida.")
        atual = _pagina_do_painel(page.context, page)
        u = _url_da_pagina(atual)
        if "/dashboard" in u or "/notas/" in u:
            visto += 1
            if visto >= 2:
                log("Painel detectado. Indo para NFS-e Recebidas…")
                try:
                    atual.bring_to_front()
                except Exception:
                    pass
                atual.wait_for_timeout(1500)
                return atual
        else:
            visto = 0
        try:
            page.wait_for_timeout(1200)
        except Exception:
            time.sleep(1.2)
    raise TimeoutError("Tempo esgotado aguardando o login no portal.")


def _forcar_tela_login(page: Page, log: LogFn) -> None:
    """Sempre abre a tela de login: o usuário entra com senha ou certificado."""
    try:
        page.context.clear_cookies()
    except Exception:
        pass
    log("Abrindo a tela de login do Emissor Nacional…")
    try:
        page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        pass
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except PlaywrightTimeout:
        pass


def _na_recebidas(page: Page) -> bool:
    url = (page.url or "").lower()
    if "recebidas" in url:
        return True
    try:
        if page.locator("#datainicio, input[name='datainicio'], input[id*='DataInicial' i]").count() > 0:
            return True
    except Exception:
        pass
    return False


_JS_CLICAR_RECEBIDAS = r"""
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

  const hrefs = [...document.querySelectorAll("a[href*='Recebidas'], a[href*='recebidas']")];
  if (hrefs.length) {
    hrefs[0].click();
    return hrefs[0].getAttribute("href") || "href Recebidas";
  }

  const re = /recebidas/i;
  const clicaveis = [...document.querySelectorAll("a, button, [role='link'], [role='button']")];
  for (const el of clicaveis) {
    const t = attr(el);
    if (!re.test(t)) continue;
    el.click();
    return t || "Recebidas";
  }

  const filhos = [...document.querySelectorAll("img, i, span, svg, em")];
  for (const el of filhos) {
    const t = attr(el) + " " + (el.className || "");
    if (!re.test(t) && !/arrow-down|seta.*baix/i.test(t)) continue;
    if (!re.test(attr(el)) && !re.test(attr(el.closest("a, button") || el))) continue;
    const alvo = el.closest("a, button") || el;
    alvo.click();
    return attr(alvo) || "ícone Recebidas";
  }

  const visiveisTopo = [...document.querySelectorAll("header a, .navbar a, nav a, .navbar-right a")]
    .filter((a) => {
      const r = a.getBoundingClientRect();
      return r.width >= 16 && r.height >= 16 && r.top >= 0 && r.top < 140 && r.right > window.innerWidth * 0.35;
    })
    .sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
  if (visiveisTopo.length >= 4) {
    visiveisTopo[3].click();
    return "4º ícone do topo (NFS-e Recebidas)";
  }
  return "";
}
"""


def _abrir_menu_navegacao(page: Page) -> None:
    toggles = [
        "button.navbar-toggle",
        "button.navbar-toggler",
        ".navbar-toggle",
        "button[data-toggle='collapse']",
        "button[data-bs-toggle='collapse']",
        "a.navbar-toggle",
        ".fa-bars",
        "i.fa-bars",
        "#btnMenu",
        ".hamburger",
        "button[aria-label*='menu' i]",
        "button[aria-label*='naveg' i]",
    ]
    for sel in toggles:
        loc = page.locator(sel)
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=2_000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def _clicar_icone_recebidas(page: Page, log: LogFn) -> bool:
    try:
        clicado = page.evaluate(_JS_CLICAR_RECEBIDAS) or ""
    except Exception:
        clicado = ""
    if not clicado:
        return False
    log(f"Cliquei no ícone de NFS-e Recebidas ({clicado}).")
    try:
        page.wait_for_url(re.compile(r"Notas/Recebidas", re.I), timeout=10_000)
    except PlaywrightTimeout:
        page.wait_for_timeout(800)
    return "recebidas" in _url_norm(page) or _na_recebidas(page)


def _ir_para_recebidas(page: Page, log: LogFn) -> Page:
    page = _pagina_do_painel(page.context, page)
    try:
        page.bring_to_front()
    except Exception:
        pass
    if "recebidas" in _url_norm(page):
        return page

    if "/dashboard" in _url_norm(page):
        log("Estou no Dashboard. As notas recebidas não ficam nessa tela — vou abrir NFS-e Recebidas.")
        if _clicar_icone_recebidas(page, log):
            return page

    log(f"Abrindo NFS-e Recebidas: {URL_RECEBIDAS}")
    try:
        page.goto(URL_RECEBIDAS, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        log(f"Não abri pelo endereço ({e}). Tentando o ícone…")
        if _clicar_icone_recebidas(page, log):
            return page
        _salvar_evidencia(page, log, "falha ao abrir Recebidas")
        raise RuntimeError(f"Não abri a tela de NFS-e Recebidas: {e}") from e

    try:
        page.wait_for_url(re.compile(r"Notas/Recebidas", re.I), timeout=15_000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(800)
    _esperar_se_pesado(page, log)

    if "recebidas" in _url_norm(page):
        log("Tela de NFS-e Recebidas aberta.")
        return page
    if _na_tela_login(page):
        _salvar_evidencia(page, log, "voltou para o login")
        raise RuntimeError(
            "Voltei para o login ao abrir NFS-e Recebidas. Faça o login de novo no Chrome."
        )
    if "/dashboard" in _url_norm(page):
        log("Ainda no Dashboard; clicando no 4º ícone do topo e tentando o endereço de novo…")
        if _clicar_icone_recebidas(page, log):
            return page
        page.goto(URL_RECEBIDAS, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(1000)
        if "recebidas" in _url_norm(page):
            return page

    for _ in range(12):
        if _localizar_data(page, "inicio") is not None:
            log("Campos de data encontrados — seguindo.")
            return page
        page.wait_for_timeout(400)

    _salvar_evidencia(page, log, "não chegou em Recebidas")
    raise RuntimeError(
        "Não cheguei em /EmissorNacional/Notas/Recebidas. "
        f"Página atual: {page.url}"
    )


def _localizar_data(page: Page, qual: str) -> Locator | None:
    ids = {
        "inicio": [
            "#datainicio",
            "input[name='datainicio']",
            "input[id*='DataInicial' i]",
            "input[name*='DataInicial' i]",
            "input[id*='datainicial' i]",
            "input[name*='datainicial' i]",
            "input[placeholder*='inicial' i]",
        ],
        "fim": [
            "#datafim",
            "input[name='datafim']",
            "input[id*='DataFinal' i]",
            "input[name*='DataFinal' i]",
            "input[id*='datafinal' i]",
            "input[name*='datafinal' i]",
            "input[placeholder*='final' i]",
        ],
    }
    labels = {
        "inicio": re.compile(r"Data\s+inicial", re.I),
        "fim": re.compile(r"Data\s+final", re.I),
    }
    for sel in ids[qual]:
        loc = page.locator(sel)
        if loc.count() > 0:
            return loc.first
    try:
        loc = page.get_by_label(labels[qual])
        if loc.count() > 0:
            return loc.first
    except Exception:
        pass
    return None


def _preencher_data(loc: Locator, valor: str) -> None:
    loc.click(timeout=8_000)
    loc.page.wait_for_timeout(random.randint(150, 400))
    loc.press("Control+A")
    loc.press("Backspace")
    loc.type(valor, delay=random.randint(45, 110))
    loc.page.wait_for_timeout(random.randint(180, 450))
    loc.press("Tab")


def _salvar_evidencia(page: Page, log: LogFn, motivo: str) -> None:
    try:
        from app.bootstrap import pasta_dados

        pasta = pasta_dados() / "erros"
        pasta.mkdir(parents=True, exist_ok=True)
        dest = pasta / "ultima_tela.png"
        page.screenshot(path=str(dest), full_page=True)
        log(f"Captura da tela salva em {dest}")
        (pasta / "ultima_url.txt").write_text(f"{motivo}\n{page.url}\n", encoding="utf-8")
    except Exception:
        pass


def aplicar_filtro(page: Page, inicio: date, fim: date, log: LogFn) -> None:
    ini_s, fim_s = fmt_br(inicio), fmt_br(fim)
    log(f"Preenchendo no portal o período do Apanhador: {ini_s} a {fim_s}.")
    log(f"Página atual: {page.url}")
    campo_ini = _localizar_data(page, "inicio")
    campo_fim = _localizar_data(page, "fim")
    if campo_ini is None or campo_fim is None:
        _salvar_evidencia(page, log, "campos de data não encontrados")
        raise RuntimeError(
            "Cheguei na tela, mas não achei as caixas Data inicial / Data final do portal "
            "para colar o período que você escolheu no Apanhador. "
            f"Página atual: {page.url}"
        )
    _preencher_data(campo_ini, ini_s)
    _preencher_data(campo_fim, fim_s)

    botao = page.get_by_role("button", name=RE_FILTRAR)
    if botao.count() == 0:
        botao = page.locator("button, input[type=submit], a.btn").filter(has_text=re.compile(r"Filtrar", re.I))
    _pausa_humana(page, 400, 900)
    if not _clicar(botao):
        raise RuntimeError("Não consegui clicar em Filtrar.")
    _pausa_humana(page, 1100, 1800)
    _esperar_se_pesado(page, log)


def _linhas(page: Page) -> Locator:
    loc = page.locator("table tbody tr[data-chave]")
    if loc.count() > 0:
        return loc
    return page.locator("table.table tbody tr, table tbody tr")


def _linha_util(row: Locator) -> bool:
    try:
        txt = (row.inner_text() or "").strip().lower()
    except Exception:
        return False
    if not txt:
        return False
    if "nenhum" in txt and "registro" in txt:
        return False
    if "não foram encontradas" in txt or "nao foram encontradas" in txt:
        return False
    return True


def _detalhe_carregou(page: Page) -> bool:
    try:
        if page.get_by_text(re.compile(r"Emitente|Tributação Municipal|Razão Social", re.I)).count() > 0:
            return True
        if "visualizar" in (page.url or "").lower():
            texto = page.locator("body").inner_text(timeout=700) or ""
            if re.search(r"acesso negado|access denied|erro interno|forbidden", texto, re.I):
                return False
            return bool(
                re.search(r"chave de acesso|compet[eê]ncia|dados da nfs-?e", texto, re.I)
            )
    except Exception:
        pass
    return False


_JS_CHAVES_LISTA = r"""
() => {
  const seen = new Set();
  const chaves = [];
  const add = (raw) => {
    const s = String(raw || "").replace(/\D/g, "");
    if (s.length >= 44 && !seen.has(s)) {
      seen.add(s);
      chaves.push(s);
    }
  };
  for (const tr of document.querySelectorAll("tr[data-chave], [data-chave]")) {
    add(tr.getAttribute("data-chave"));
  }
  for (const a of document.querySelectorAll("a[href*='Visualizar']")) {
    const href = a.getAttribute("href") || a.href || "";
    const m = href.match(/Visualizar\/Index\/(\d+)/i) || href.match(/(\d{44,50})/);
    if (m) add(m[1]);
  }
  if (chaves.length) return chaves;
  const html = document.body ? document.body.innerHTML : "";
  for (const m of html.matchAll(/Visualizar\/Index\/(\d{44,50})/gi)) add(m[1]);
  return chaves;
}
"""


def _chave_da_linha(row: Locator) -> str:
    try:
        chave = row.get_attribute("data-chave") or ""
        chave_numerica = re.sub(r"\D", "", chave)
        if 44 <= len(chave_numerica) <= 50:
            return chave_numerica
    except Exception:
        pass
    try:
        href = ""
        link = row.locator('a[href*="Visualizar"]')
        if link.count() > 0:
            href = link.first.get_attribute("href") or ""
        m = re.search(r"Visualizar/Index/(\d+)", href, re.I) or re.search(r"(\d{44,50})", href)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        html = row.inner_html()
        m = re.search(r"Visualizar/Index/(\d{44,50})", html, re.I) or re.search(r"(\d{44,50})", html)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _garantir_lista_antes(page: Page, url_lista: str) -> None:
    if not _na_lista_recebidas(page):
        _voltar_lista(page, url_lista)
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    _pausa_humana(page, 350, 750)


def _pagina_de_detalhe_apos_clique(page: Page, alvo: Locator) -> Page | None:
    """Clica como o portal espera e aceita detalhe na aba atual ou em nova aba."""
    paginas_antes = set(page.context.pages)
    if not _clicar(alvo):
        return None

    fim = time.monotonic() + 28
    while time.monotonic() < fim:
        novas = [p for p in page.context.pages if p not in paginas_antes and not p.is_closed()]
        for candidata in [*reversed(novas), page]:
            if not candidata.is_closed() and _detalhe_carregou(candidata):
                try:
                    candidata.bring_to_front()
                except Exception:
                    pass
                _pausa_humana(candidata, 600, 1200)
                return candidata
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)

    # Não deixa abas de erro acumularem durante a coleta.
    for extra in page.context.pages:
        if extra not in paginas_antes and extra is not page:
            try:
                extra.close()
            except Exception:
                pass
    return None


_JS_CLICAR_VISUALIZAR_LINHA = r"""
(tr) => {
  const click = (el) => {
    if (!el) return;
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    if (typeof el.click === "function") el.click();
  };
  const toggles = tr.querySelectorAll(
    'a[data-toggle="dropdown"], button[data-toggle="dropdown"], ' +
    'a[data-bs-toggle="dropdown"], button[data-bs-toggle="dropdown"], ' +
    ".dropdown-toggle, i.fa-ellipsis-v, i.fa-ellipsis"
  );
  for (const t of toggles) {
    click(t.closest("a,button") || t);
  }
  const menus = document.querySelectorAll(
    ".dropdown-menu.show, .dropdown.open .dropdown-menu, .dropdown-menu.open"
  );
  for (const menu of menus) {
    for (const a of menu.querySelectorAll("a")) {
      const href = a.getAttribute("href") || "";
      const txt = (a.innerText || a.textContent || "").trim();
      if (/visualizar/i.test(txt) || /visualizar/i.test(href)) {
        click(a);
        return true;
      }
    }
  }
  const direto = tr.querySelector('a[href*="Visualizar"]');
  if (direto) {
    click(direto);
    return true;
  }
  return false;
}
"""


def _clicar_visualizar_js(row: Locator) -> bool:
    try:
        return bool(row.evaluate(_JS_CLICAR_VISUALIZAR_LINHA))
    except Exception:
        return False


def _clicar_visualizar_na_linha(page: Page, row: Locator) -> Page | None:
    try:
        row.scroll_into_view_if_needed(timeout=3_000)
        row.hover(timeout=3_000)
        _pausa_humana(page, 200, 500)
    except Exception:
        pass

    tres_pontos = row.locator(
        'a.dropdown-toggle, button.dropdown-toggle, a[data-toggle="dropdown"], '
        'button[data-toggle="dropdown"], a[data-bs-toggle="dropdown"], '
        'button[data-bs-toggle="dropdown"], .dropdown > a, '
        'a:has(i.fa-ellipsis-v), button:has(i.fa-ellipsis-v), '
        'a:has(i.fa-ellipsis), button:has(i.fa-ellipsis), i.fa-ellipsis-v'
    )
    if tres_pontos.count() > 0:
        _clicar(tres_pontos)
        _pausa_humana(page, 450, 900)

    vis = page.locator(
        ".dropdown.open .dropdown-menu a[href*='Visualizar']:visible, "
        ".dropdown-menu.show a[href*='Visualizar']:visible, "
        ".dropdown-menu:visible a[href*='Visualizar']:visible"
    )
    if not _visivel(vis):
        vis = page.locator(
            ".dropdown.open .dropdown-menu a:visible, "
            ".dropdown-menu.show a:visible, .dropdown-menu:visible a:visible"
        ).filter(has_text=RE_VISUALIZAR)
    if not _visivel(vis):
        vis = row.locator("a[href*='Visualizar']:visible")
    if _visivel(vis):
        det = _pagina_de_detalhe_apos_clique(page, vis)
        if det is not None:
            return det

    if _clicar_visualizar_js(row):
        _pausa_humana(page, 800, 1500)
        fim = time.monotonic() + 28
        paginas_antes = set(page.context.pages)
        while time.monotonic() < fim:
            novas = [p for p in page.context.pages if p not in paginas_antes and not p.is_closed()]
            for candidata in [*reversed(novas), page]:
                if not candidata.is_closed() and _detalhe_carregou(candidata):
                    try:
                        candidata.bring_to_front()
                    except Exception:
                        pass
                    _pausa_humana(candidata, 600, 1200)
                    return candidata
            page.wait_for_timeout(250)
    return None


def _linha_por_chave(page: Page, chave: str) -> Locator | None:
    chave = re.sub(r"\D", "", chave or "")
    if not chave:
        return None
    loc = page.locator(f'table tbody tr[data-chave="{chave}"]')
    if loc.count() > 0:
        return loc.first

    link = page.locator(f'table tbody tr a[href*="{chave}"]')
    if link.count() > 0:
        row_link = link.first.locator("xpath=ancestor::tr[1]")
        if row_link.count() > 0:
            return row_link

    rows = page.locator("table.table tbody tr, table tbody tr")
    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            if _chave_da_linha(row) == chave:
                return row
        except Exception:
            continue
    return None


def _abrir_visualizar(page: Page, row: Locator, url_lista: str, log: LogFn) -> Page | None:
    chave = _chave_da_linha(row)
    if chave:
        return _abrir_por_chave(page, chave, url_lista, log)

    _garantir_lista_antes(page, url_lista)
    log("    Clicando em Visualizar na listagem…")
    detalhe = _clicar_visualizar_na_linha(page, row)
    if detalhe is not None:
        return detalhe
    log("Não achei o Visualizar desta linha.")
    return None


def _na_lista_recebidas(page: Page) -> bool:
    u = _url_norm(page)
    if "visualizar" in u or "/dashboard" in u or "/login" in u:
        return False
    if "recebidas" in u:
        return True
    if "emitid" in u or "modelo" in u:
        return False
    return _localizar_data(page, "inicio") is not None


def _alvo_lista_recebidas(url_lista: str) -> str:
    u = (url_lista or "").strip()
    if u and "recebidas" in u.lower() and "visualizar" not in u.lower() and "/dashboard" not in u.lower():
        return u
    return URL_RECEBIDAS


def _voltar_lista(page: Page, url_lista: str) -> None:
    """Volta ao home de NFS-e Recebidas — nunca ao Dashboard (home geral)."""
    if _na_lista_recebidas(page):
        return

    alvo = _alvo_lista_recebidas(url_lista)

    # Voltar pelo histórico preserva o filtro e a página da tabela, como faria
    # uma pessoa no Chrome. É especialmente importante após uma resposta de
    # erro do detalhe.
    try:
        page.go_back(wait_until="domcontentloaded", timeout=20_000)
        _pausa_humana(page, 500, 1000)
        if _na_lista_recebidas(page):
            return
    except Exception:
        pass

    try:
        voltar = page.locator(
            "a, button, input[type=button], input[type=submit]"
        ).filter(has_text=re.compile(r"^\s*Voltar\s*$", re.I))
        if _visivel(voltar):
            href = (voltar.first.get_attribute("href") or "").lower()
            if "dashboard" not in href and "home" not in href:
                _clicar(voltar)
                page.wait_for_timeout(500)
                if _na_lista_recebidas(page):
                    return
    except Exception:
        pass

    for destino in (alvo, URL_RECEBIDAS):
        try:
            page.goto(destino, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            continue
        page.wait_for_timeout(600)
        if "/dashboard" in _url_norm(page):
            try:
                page.goto(URL_RECEBIDAS, wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_timeout(600)
            except Exception:
                pass
        if _na_lista_recebidas(page):
            return


def _ativar_aba(page: Page, nome: str, *hrefs: str) -> bool:
    """Troca aba DENTRO da nota. Não clica no menu/home geral do portal."""
    abas = (
        ".nav-tabs a, .nav-pills a, [role='tab'], "
        "a[data-toggle='tab'], a[data-bs-toggle='tab']"
    )
    padroes: list[Locator] = [
        page.locator(abas).filter(has_text=re.compile(rf"^{re.escape(nome)}$", re.I)),
        page.get_by_role("tab", name=re.compile(rf"^{re.escape(nome)}$", re.I)),
    ]
    for href in hrefs:
        if href.startswith("#"):
            padroes.append(page.locator(f"a[href='{href}']"))
        else:
            padroes.append(page.locator(abas).filter(has=page.locator(f"[href='{href}']")))
    url_antes = page.url or ""
    for loc in padroes:
        if not _visivel(loc):
            continue
        try:
            href = (loc.first.get_attribute("href") or "").lower()
            no_topo = loc.first.evaluate(
                """el => !!(el.closest('header, .navbar, nav.navbar, .navbar-nav, .navbar-header, .navbar-right'))"""
            )
        except Exception:
            href = ""
            no_topo = False
        if no_topo:
            continue
        if href.startswith("http") and "visualizar" not in href and not href.startswith("#"):
            continue
        if "/dashboard" in href or href.rstrip("/").endswith("/emissornacional"):
            continue
        if _clicar(loc):
            page.wait_for_timeout(450)
            url_depois = _url_norm(page)
            if url_depois != url_antes.lower() and "visualizar" not in url_depois:
                try:
                    page.goto(url_antes, wait_until="domcontentloaded", timeout=30_000)
                except Exception:
                    pass
                continue
            return True
    return False


def _chave_da_url(url: str) -> str:
    try:
        m = re.search(r"Visualizar/Index/(\d{44,50})", url or "", re.I)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _numero_da_chave(chave: str) -> str:
    """Extrai o número oficial (posições 24–36) da chave nacional de 50 posições."""
    chave_limpa = re.sub(r"\D", "", chave or "")
    if len(chave_limpa) != 50:
        return ""
    numero = chave_limpa[23:36].lstrip("0")
    return numero or "0"


def _data_de_texto(texto: str) -> str:
    """Busca a data de emissão no texto da linha/lista e normaliza a hora.

    O portal nacional pode expor a data de emissão no rótulo textual e a
    mesma linha inclui marca de tempo `HH:MM:SS`. O valor exportado para a
    planilha precisa cair como `DD/MM/YYYY`, sem deixar a célula em branco.
    """
    if not texto:
        return ""
    txt = str(texto)
    m = re.search(r"\b\d{2}/\d{2}/(?:\d{4}|\d{2})(?:\s+\d{2}:\d{2}(?::\d{2})?)?\b", txt)
    if m:
        data = m.group(0).split()[0]
        partes = data.split("/")
        if len(partes[2]) == 2:
            data = f"{partes[0]}/{partes[1]}/20{partes[2]}"
        return data
    return ""


def _competencia_de_texto(texto: str) -> str:
    """Extrai o mês/ano de competência apenas quando o rótulo está explícito.

    A fonte de texto pode vir da linha da lista, do contexto da página ou do
    bloco de detalhe. O objetivo é ignorar datas de emissão DD/MM/YYYY e
    devolver a competência no formato `MM/AAAA` somente se houver rótulo de
    competência explícito no texto.
    """
    if not texto:
        return ""

    txt = str(texto)
    txt_norm = "\n".join(linha.strip() for linha in txt.replace("\r", "\n").split("\n") if linha.strip())

    # Prioridade 1: rótulo explícito de competência com o padrão de MM/AAAA.
    for padrao in (
        r"Compet[eê]ncia\s+de\s+Emiss[aã]o\s*[:\-]?\s*(\d{2}/\d{4})",
        r"Compet[eê]ncia\s*[:\-]?\s*(\d{2}/\d{4})",
        r"Competencia\s+de\s+Emissao\s*[:\-]?\s*(\d{2}/\d{4})",
        r"Competencia\s*[:\-]?\s*(\d{2}/\d{4})",
    ):
        m = re.search(padrao, txt_norm, re.I)
        if m:
            return m.group(1)

    # Prioridade 2: se a linha de texto tiver o rótulo e o mês/ano logo em
    # seguida, reconhecer a competência apenas no mesmo contexto da linha.
    for linha in txt_norm.split("\n"):
        if re.search(r"compet[eê]ncia|competencia", linha, re.I):
            m = re.search(r"\b\d{2}/\d{4}\b", linha)
            if m:
                return m.group(0)

    # Prioridade 3: procurar o rótulo e o valor no bloco completo. Essa escada
    # nunca deve aceitar o formato de emissão DD/MM/YYYY.
    melhor = re.search(r"compet[eê]ncia|competencia", txt, re.I)
    if melhor:
        trecho = txt[melhor.start():]
        m = re.search(r"\b\d{2}/\d{4}\b", trecho)
        if m:
            return m.group(0)

    return ""


def _codigo_tributacao_de_texto(texto: str) -> str:
    if not texto:
        return ""
    txt = str(texto).replace("\r", "\n")
    for padrao in (
        r"C[oó]digo de Tributa[cç][aã]o Nacional[^\d]{0,80}(\d{6}\s*-\s*.+?)(?=\n[A-ZÁÉÍÓÚ]|$)",
        r"(\d{6}\s*-\s*.+)",
    ):
        m = re.search(padrao, txt, re.I | re.S)
        if m:
            valor = re.sub(r"\s+", " ", m.group(1)).strip()
            if len(valor) > 10 and re.match(r"\d{6}\s*-", valor):
                return valor
    return ""


def _normalizar_rotulo(texto: str) -> str:
    sem_acentos = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", sem_acentos).strip().lower()


def _colunas_da_linha(row: Locator) -> dict[str, str]:
    """Associa cada célula da linha ao cabeçalho visível da tabela."""
    try:
        tabela = row.locator("xpath=ancestor::table[1]")
        cabecalhos = tabela.locator("thead th")
        celulas = row.locator("td")
        if cabecalhos.count() == 0 or celulas.count() == 0:
            return {}
        nomes = [
            _normalizar_rotulo(cabecalhos.nth(i).inner_text() or "")
            for i in range(cabecalhos.count())
        ]
        valores = [
            (celulas.nth(i).inner_text() or "").strip()
            for i in range(celulas.count())
        ]
        return {
            nome: valores[i]
            for i, nome in enumerate(nomes)
            if nome and i < len(valores)
        }
    except Exception:
        return {}


def _valor_da_coluna(colunas: dict[str, str], *nomes: str) -> str:
    procurados = tuple(_normalizar_rotulo(nome) for nome in nomes)
    for cabecalho, valor in colunas.items():
        if any(nome in cabecalho for nome in procurados):
            return (valor or "").strip()
    return ""


def _parse_moeda_br(texto: str) -> float | None:
    bruto = (texto or "").strip()
    if not bruto:
        return None
    if isinstance(bruto, (int, float)):
        return float(bruto)
    limpo = re.sub(r"[R$\s]", "", bruto, flags=re.I)
    limpo = limpo.replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _formatar_moeda_br(valor: float) -> str:
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _valor_liquido_de_texto(texto: str) -> str:
    if not texto:
        return ""
    for padrao in (
        r"Valor\s+L[ií]quido(?:\s+da\s+NFS-?e)?(?:\s*\+\s*IBS/CBS)?[^\dR$]{0,80}R?\$?\s*"
        r"([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})",
        r"Valor\s+Liquido(?:\s+da\s+NFS-?e)?(?:\s*\+\s*IBS/CBS)?[^\dR$]{0,80}R?\$?\s*"
        r"([\d]{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})",
    ):
        m = re.search(padrao, texto, re.I)
        if m:
            return m.group(1)
    return ""


def _calcular_valor_liquido(
    valor_servico: str,
    valor_issqn: str,
    retencao: str,
    contrib_sociais: str,
    contrib_prev: str,
    irrf: str,
) -> str:
    servico = _parse_moeda_br(valor_servico)
    if servico is None:
        return ""
    issqn = _parse_moeda_br(valor_issqn) or 0.0
    cs = _parse_moeda_br(contrib_sociais) or 0.0
    inss = _parse_moeda_br(contrib_prev) or 0.0
    imp_irrf = _parse_moeda_br(irrf) or 0.0
    ret = (retencao or "").lower()
    liquido = servico - cs - inss - imp_irrf
    if "retid" in ret and "não" not in ret and "nao" not in ret:
        liquido -= issqn
    if liquido < 0:
        return ""
    return _formatar_moeda_br(liquido)


def _extrair_nota(page: Page, fallback: dict) -> dict:
    try:
        page.get_by_text(re.compile(r"Emitente|Razão Social|Tributação Municipal", re.I)).first.wait_for(
            timeout=12_000
        )
    except Exception:
        pass
    _ativar_aba(page, "NFS-e", "#nfse")
    page.wait_for_timeout(300)
    try:
        dados = page.evaluate(_JS_EXTRAIR) or {}
    except Exception:
        dados = {}

    # Mantém a compatibilidade com o parser antigo, mas incorpora a chave da URL
    # e a competência possível do campo textual da nota em um objeto de retorno.
    url_chave = _chave_da_url(page.url)
    competencia_fallback = ""
    try:
        # Tenta ler a competência do texto total da página, em vez de depender
        # de um único elemento rotulado, que costuma ficar separado do valor
        # ou não estar visível no contexto da tab de detalhe.
        texto_pagina = page.evaluate("() => (document.body && document.body.innerText) || ''") or ""
        competencia_fallback = _competencia_de_texto(texto_pagina)
    except Exception:
        competencia_fallback = ""

    if (dados.get("status") or "").strip() != "Cancelada":
        if _ativar_aba(page, "Eventos", "#eventos"):
            page.wait_for_timeout(250)
            try:
                extra = page.evaluate(_JS_EXTRAIR) or {}
                if (extra.get("status") or "").strip() == "Cancelada":
                    dados["status"] = extra.get("status")
                    dados["data_cancelamento"] = extra.get("data_cancelamento") or ""
            except Exception:
                pass
            _ativar_aba(page, "NFS-e", "#nfse")

    if _ativar_aba(page, "Outros Tributos", "#tributacao", "#outrostributos", "#outros-tributos", "#tributos"):
        try:
            page.get_by_text(
                re.compile(r"Contribui[cç][aã]o|IRRF|Tributa[cç][aã]o Federal", re.I)
            ).first.wait_for(timeout=6_000)
        except Exception:
            pass
        page.wait_for_timeout(250)
        try:
            dados.update(page.evaluate(_JS_EXTRAIR_OUTROS) or {})
        except Exception:
            pass

    if _ativar_aba(page, "Serviço", "#servico", "#Servico", "#serviço"):
        try:
            page.get_by_text(re.compile(r"C[oó]digo de Tributa[cç][aã]o Nacional", re.I)).first.wait_for(
                timeout=6_000
            )
        except Exception:
            pass
        page.wait_for_timeout(250)
        try:
            dados.update(page.evaluate(_JS_EXTRAIR_SERVICO) or {})
        except Exception:
            pass
        if not str(dados.get("codigo_tributacao_nacional") or "").strip():
            try:
                texto_servico = (
                    page.evaluate(
                        """() => {
                          const ativo = document.querySelector('.tab-pane.active, .tab-content > .active');
                          return (ativo && ativo.innerText) || (document.body && document.body.innerText) || '';
                        }"""
                    )
                    or ""
                )
                dados["codigo_tributacao_nacional"] = _codigo_tributacao_de_texto(texto_servico)
            except Exception:
                pass

    # O fallback textual da linha/lista é a última fonte de verdade para o que
    # está fora do detalhe da nota. Quando `dados` vem vazio ou incompleto,
    # o texto da própria linha que abriu a nota deve ser devolvido no objeto
    # final sem rastro de inconsistência do JS do portal.
    for campo in (
        "numero",
        "data_emissao",
        "competencia_emissao",
        "razao_emitente",
        "valor_servico",
        "valor_liquido",
        "retencao",
        "valor_issqn",
        "contrib_sociais_retidas",
        "contrib_previdenciaria_retida",
        "irrf",
        "codigo_tributacao_nacional",
        "status",
        "data_cancelamento",
    ):
        if not str(dados.get(campo) or "").strip() and str(fallback.get(campo) or "").strip():
            dados[campo] = fallback.get(campo)

    def pegar(*chaves: str) -> str:
        for c in chaves:
            v = (dados.get(c) or fallback.get(c) or "").strip()
            if v:
                return v
        return ""

    status = pegar("status") or "Ativa"
    data_cancelamento = pegar("data_cancelamento")
    if status != "Cancelada":
        status = "Ativa"
        data_cancelamento = ""

    # A chave nacional é a fonte de verdade para o número da NFS-e: as posições
    # 24–36 guardam o número oficial com 13 dígitos.
    chave_acesso = url_chave or (fallback.get("chave_acesso") or "")
    numero_chave = _numero_da_chave(chave_acesso)
    competencia_emissao = (
        (fallback.get("competencia_emissao") or "")
        or (dados.get("competencia_emissao") or "")
        or competencia_fallback
    ).strip()

    valor_liquido = pegar("valor_liquido")
    if not valor_liquido:
        valor_liquido = _valor_liquido_de_texto(texto_pagina)
    if not valor_liquido:
        valor_liquido = _calcular_valor_liquido(
            pegar("valor_servico"),
            pegar("valor_issqn"),
            pegar("retencao"),
            pegar("contrib_sociais_retidas"),
            pegar("contrib_previdenciaria_retida"),
            pegar("irrf"),
        )

    return {
        "numero": numero_chave or pegar("numero"),
        "chave_acesso": chave_acesso,
        "data_emissao": pegar("data_emissao"),
        "competencia_emissao": competencia_emissao,
        "razao_emitente": pegar("razao_emitente"),
        "codigo_tributacao_nacional": pegar("codigo_tributacao_nacional"),
        "valor_servico": pegar("valor_servico"),
        "valor_liquido": valor_liquido,
        "retencao": pegar("retencao"),
        "valor_issqn": pegar("valor_issqn"),
        "contrib_sociais_retidas": pegar("contrib_sociais_retidas"),
        "contrib_previdenciaria_retida": pegar("contrib_previdenciaria_retida"),
        "irrf": pegar("irrf"),
        "status": status,
        "data_cancelamento": data_cancelamento,
    }


def _valor_monetario_de_texto(texto: str) -> str:
    if not texto:
        return ""
    for padrao in (
        r"R\$\s*([\d.]+,\d{2})",
        r"\b(\d{1,3}(?:\.\d{3})*,\d{2})\b",
    ):
        m = re.search(padrao, texto)
        if m:
            return m.group(1)
    return ""


def _fallback_da_linha(row: Locator) -> dict:
    texto = ""
    try:
        texto = row.inner_text() or ""
    except Exception:
        texto = ""
    data = _data_de_texto(texto)
    chave = _chave_da_linha(row)
    colunas = _colunas_da_linha(row)
    competencia = (
        _valor_da_coluna(colunas, "competência", "competencia")
        or _competencia_de_texto(texto)
    )
    if not competencia:
        for parte in (p.strip() for p in texto.split("\n") if p.strip()):
            if re.fullmatch(r"\d{2}/\d{4}", parte):
                competencia = parte
                break
    valor_servico = _valor_da_coluna(
        colunas,
        "preço serviço",
        "preco servico",
        "valor do serviço",
        "valor do servico",
    ) or _valor_monetario_de_texto(texto)
    emitida_por = _valor_da_coluna(colunas, "emitida por", "prestador", "emitente")
    razao_emitente = emitida_por
    if " - " in emitida_por:
        razao_emitente = emitida_por.split(" - ", 1)[1].strip()

    status = _valor_da_coluna(colunas, "situação", "situacao", "status")
    if not status:
        try:
            marcadores = row.locator("[title], [aria-label]")
            for i in range(marcadores.count()):
                titulo = (
                    marcadores.nth(i).get_attribute("title")
                    or marcadores.nth(i).get_attribute("aria-label")
                    or ""
                ).strip()
                if re.search(r"cancelad|ativa|substitu[ií]d", titulo, re.I):
                    status = titulo
                    break
        except Exception:
            status = ""
    return {
        "numero": _numero_da_chave(chave),
        "chave_acesso": chave,
        "data_emissao": data,
        "competencia_emissao": competencia,
        "razao_emitente": razao_emitente,
        "valor_servico": valor_servico,
        "valor_liquido": "",
        "retencao": "",
        "valor_issqn": "",
        "contrib_sociais_retidas": "",
        "contrib_previdenciaria_retida": "",
        "irrf": "",
        "codigo_tributacao_nacional": "",
        "status": status,
        "data_cancelamento": "",
    }


def _fallback_da_chave(page: Page, chave: str) -> dict:
    row = _linha_por_chave(page, chave)
    if row is not None:
        return _fallback_da_linha(row)
    return {
        "chave_acesso": chave,
        "competencia_emissao": "",
        "data_emissao": "",
        "numero": _numero_da_chave(chave),
        "razao_emitente": "",
        "valor_servico": "",
        "valor_liquido": "",
        "retencao": "",
        "valor_issqn": "",
        "contrib_sociais_retidas": "",
        "contrib_previdenciaria_retida": "",
        "irrf": "",
        "codigo_tributacao_nacional": "",
        "status": "",
        "data_cancelamento": "",
    }


def _mesclar_nota(fallback: dict, detalhe: dict) -> dict:
    base = _nota_somente_da_listagem(fallback)
    for campo, valor in detalhe.items():
        if str(valor or "").strip():
            base[campo] = valor
    return base


def _nota_somente_da_listagem(fallback: dict) -> dict:
    """Produz uma linha útil quando o portal bloqueia a página de detalhe."""
    status = (fallback.get("status") or "").strip()
    if not status or status.lower() == "não verificado":
        status = "Ativa"
    return {
        "numero": _numero_da_chave(fallback.get("chave_acesso") or "")
        or (fallback.get("numero") or ""),
        "chave_acesso": fallback.get("chave_acesso") or "",
        "data_emissao": fallback.get("data_emissao") or "",
        "competencia_emissao": fallback.get("competencia_emissao") or "",
        "razao_emitente": fallback.get("razao_emitente") or "",
        "valor_servico": fallback.get("valor_servico") or "",
        "valor_liquido": "",
        "retencao": "",
        "valor_issqn": "",
        "contrib_sociais_retidas": "",
        "contrib_previdenciaria_retida": "",
        "irrf": "",
        "codigo_tributacao_nacional": "",
        "status": status,
        "data_cancelamento": fallback.get("data_cancelamento") or "",
    }


def _proxima_pagina(page: Page) -> bool:
    links = page.locator(".pagination li:not(.disabled) a, ul.pagination li:not(.disabled) a")
    n = links.count()
    for i in range(n):
        link = links.nth(i)
        try:
            if link.locator("i.fa-angle-right:not(.fa-angle-double-right), i.fa-chevron-right").count() > 0:
                link.click(timeout=8_000)
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    vis = page.get_by_role("link", name=re.compile(r"Próxima|Proxima", re.I))
    if _visivel(vis) and _clicar(vis):
        page.wait_for_timeout(800)
        return True
    return False


def _chaves_da_pagina(page: Page) -> list[str]:
    try:
        chaves = page.evaluate(_JS_CHAVES_LISTA) or []
        return [c for c in chaves if isinstance(c, str) and len(c) >= 44]
    except Exception:
        return []


def _tentar_abrir_nota(page: Page, chave: str, log: LogFn) -> Page | None:
    row = _linha_por_chave(page, chave)
    if row is not None:
        log("    Abrindo pelo menu de ações da nota…")
        return _clicar_visualizar_na_linha(page, row)

    link = page.locator(f'a[href*="{chave}"]:visible')
    if _visivel(link):
        log("    Abrindo pelo link visível da nota…")
        return _pagina_de_detalhe_apos_clique(page, link)

    return None


def _abrir_por_chave(page: Page, chave: str, url_lista: str, log: LogFn) -> Page | None:
    _garantir_lista_antes(page, url_lista)
    pagina_nota = _tentar_abrir_nota(page, chave, log)
    if pagina_nota is not None:
        return pagina_nota

    log("    O detalhe não abriu na primeira tentativa. Esperando e tentando de novo…")
    _pausa_humana(page, 12_000, 20_000)
    _garantir_lista_antes(page, url_lista)
    pagina_nota = _tentar_abrir_nota(page, chave, log)
    if pagina_nota is None:
        log("    Não consegui abrir o detalhe desta nota.")
    return pagina_nota


def _coletar_periodo(page: Page, inicio: date, fim: date, parar: Event, log: LogFn) -> list[dict]:
    if "recebidas" not in _url_norm(page):
        raise RuntimeError(
            "A listagem de recebidas ainda não abriu; não vou clicar nas notas do Dashboard. "
            f"Página atual: {page.url}"
        )
    aplicar_filtro(page, inicio, fim, log)
    url_lista = page.url
    notas: list[dict] = []
    pagina = 1
    while True:
        if parar.is_set():
            break
        chaves = _chaves_da_pagina(page)
        linhas = _linhas(page)
        total = linhas.count()
        uteis = [i for i in range(total) if _linha_util(linhas.nth(i))]
        if not chaves and not uteis:
            log(f"Nenhuma nota neste período ({fmt_br(inicio)} a {fmt_br(fim)}).")
            break

        if chaves:
            log(f"Página {pagina}: {len(chaves)} nota(s) (pela chave de acesso).")
            # Guarda os dados de todas as linhas antes de sair da listagem.
            # Assim uma falha de navegação não apaga as notas seguintes.
            fallbacks = {chave: _fallback_da_chave(page, chave) for chave in chaves}
            for pos, chave in enumerate(chaves, start=1):
                if parar.is_set():
                    return notas
                if pos > 1:
                    _pausa_entre_notas(page, log)
                else:
                    _pausa_humana(page, 3000, 6000)
                log(f"  Abrindo nota {pos}/{len(chaves)} da página {pagina}…")
                fallback = fallbacks[chave]
                _garantir_lista_antes(page, url_lista)
                pagina_nota = _abrir_por_chave(page, chave, url_lista, log)
                if pagina_nota is None:
                    notas.append(_nota_somente_da_listagem(fallback))
                    log(
                        "    Incluída só com dados da listagem. Retenções, ISSQN e valor "
                        "líquido dependem do detalhe — sigo tentando nas próximas notas."
                    )
                    _voltar_lista(page, url_lista)
                    _pausa_humana(page, 5000, 9000)
                    continue
                try:
                    _esperar_se_pesado(pagina_nota, log)
                    _pausa_humana(pagina_nota, 2500, 5000)
                    nota = _extrair_nota(pagina_nota, fallback)
                    notas.append(nota)
                    log(
                        f"    Nº {nota.get('numero') or '?'}  |  "
                        f"{nota.get('razao_emitente') or '?'}  |  "
                        f"{nota.get('valor_servico') or '?'}  |  "
                        f"Líquido {nota.get('valor_liquido') or '-'}  |  "
                        f"ISS {nota.get('valor_issqn') or '-'}  |  "
                        f"{nota.get('retencao') or '-'}  |  "
                        f"{nota.get('status') or 'Ativa'}"
                        + (
                            f" ({nota.get('data_cancelamento')})"
                            if nota.get("data_cancelamento")
                            else ""
                        )
                        + "  |  "
                        f"CS {nota.get('contrib_sociais_retidas') or '-'}  |  "
                        f"INSS {nota.get('contrib_previdenciaria_retida') or '-'}  |  "
                        f"IRRF {nota.get('irrf') or '-'}"
                    )
                except Exception as e:
                    log(f"    Falha ao ler a nota: {e}")
                    notas.append(_nota_somente_da_listagem(fallback))
                    log("    Nota incluída com os dados disponíveis na listagem.")
                finally:
                    _pausa_humana(pagina_nota, 1500, 3000)
                    if pagina_nota is page:
                        _voltar_lista(page, url_lista)
                    else:
                        try:
                            pagina_nota.close()
                            page.bring_to_front()
                        except Exception:
                            pass
                    _pausa_humana(page, 1000, 2200)
        else:
            log(f"Página {pagina}: {len(uteis)} nota(s).")
            fallbacks_linhas = [_fallback_da_linha(linhas.nth(idx)) for idx in uteis]
            for pos, idx in enumerate(uteis, start=1):
                if parar.is_set():
                    return notas
                if pos > 1:
                    _pausa_entre_notas(page, log)
                else:
                    _pausa_humana(page, 3000, 6000)
                linhas = _linhas(page)
                row = linhas.nth(idx)
                fallback = fallbacks_linhas[pos - 1]
                log(f"  Abrindo nota {pos}/{len(uteis)} da página {pagina}…")
                _garantir_lista_antes(page, url_lista)
                pagina_nota = _abrir_visualizar(page, row, url_lista, log)
                if pagina_nota is None:
                    notas.append(_nota_somente_da_listagem(fallback))
                    log(
                        "    Incluída só com dados da listagem. Retenções, ISSQN e valor "
                        "líquido dependem do detalhe — sigo tentando nas próximas notas."
                    )
                    _voltar_lista(page, url_lista)
                    _pausa_humana(page, 5000, 9000)
                    continue
                try:
                    _esperar_se_pesado(pagina_nota, log)
                    _pausa_humana(pagina_nota, 2500, 5000)
                    nota = _extrair_nota(pagina_nota, fallback)
                    notas.append(nota)
                    log(
                        f"    Nº {nota.get('numero') or '?'}  |  "
                        f"{nota.get('razao_emitente') or '?'}  |  "
                        f"{nota.get('valor_servico') or '?'}  |  "
                        f"Líquido {nota.get('valor_liquido') or '-'}  |  "
                        f"ISS {nota.get('valor_issqn') or '-'}  |  "
                        f"{nota.get('retencao') or '-'}  |  "
                        f"{nota.get('status') or 'Ativa'}"
                        + (
                            f" ({nota.get('data_cancelamento')})"
                            if nota.get("data_cancelamento")
                            else ""
                        )
                        + "  |  "
                        f"CS {nota.get('contrib_sociais_retidas') or '-'}  |  "
                        f"INSS {nota.get('contrib_previdenciaria_retida') or '-'}  |  "
                        f"IRRF {nota.get('irrf') or '-'}"
                    )
                except Exception as e:
                    log(f"    Falha ao ler a nota: {e}")
                    notas.append(_nota_somente_da_listagem(fallback))
                    log("    Nota incluída com os dados disponíveis na listagem.")
                finally:
                    _pausa_humana(pagina_nota, 1500, 3000)
                    if pagina_nota is page:
                        _voltar_lista(page, url_lista)
                    else:
                        try:
                            pagina_nota.close()
                            page.bring_to_front()
                        except Exception:
                            pass
                    _pausa_humana(page, 1000, 2200)

        if not _proxima_pagina(page):
            break
        pagina += 1
        url_lista = page.url
    return notas


def executar(
    *,
    pasta_dados: Path,
    periodo: Periodo,
    parar: Event,
    confirmar_login: Event,
    log: LogFn,
) -> list[dict]:
    from playwright.sync_api import sync_playwright

    log("Abrindo o Chrome no Emissor Nacional…")
    with sync_playwright() as p:
        try:
            ctx = abrir_navegador(p, pasta_dados)
        except Exception as e:
            raise RuntimeError(
                "Não consegui abrir o Google Chrome. Confira se o Chrome está instalado "
                "e feche outras janelas que usem o mesmo perfil do Apanhador.\n"
                f"Detalhe: {e}"
            ) from e
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        todas: list[dict] = []
        try:
            _forcar_tela_login(page, log)
            page = aguardar_login(page, parar, confirmar_login, log)
            page = _ir_para_recebidas(page, log)

            fatias = periodo.fatias()
            if len(fatias) > 1:
                log(
                    "O portal aceita no máximo 30 dias por filtro. "
                    f"Vou consultar em {len(fatias)} partes."
                )
            for i, (ini, fim) in enumerate(fatias, start=1):
                if parar.is_set():
                    break
                if len(fatias) > 1:
                    log(f"Parte {i}/{len(fatias)}.")
                page = _ir_para_recebidas(page, log)
                todas.extend(_coletar_periodo(page, ini, fim, parar, log))
            log(f"Coleta concluída: {len(todas)} nota(s).")
            return todas
        except InterruptedError:
            log(f"Coleta interrompida com {len(todas)} nota(s).")
            return todas
        except Exception:
            try:
                _salvar_evidencia(page, log, "falha na coleta")
            except Exception:
                pass
            raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass
