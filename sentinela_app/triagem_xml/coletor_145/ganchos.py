"""Troca a abertura do Chrome e a espera das caixas de data."""


def abrir_navegador(playwright, pasta_dados):
    from triagem_xml.chrome_portal import JanelaChrome

    return JanelaChrome.abrir(pasta_dados).conectar(playwright)


def aplicar_filtro(page, inicio, fim, log):
    ini_s = fmt_br(inicio)
    fim_s = fmt_br(fim)
    log("Preenchendo no portal o período do Coletor: " + ini_s + " a " + fim_s + ".")
    log("Página atual: " + page.url)
    campo_ini = None
    campo_fim = None
    for tentativa in range(1, 7):
        page.wait_for_timeout(2000 if tentativa == 1 else 800)
        campo_ini = _localizar_data(page, "inicio")
        campo_fim = _localizar_data(page, "fim")
        if campo_ini is not None and campo_fim is not None:
            break
        if _portal_pesado(page):
            log(
                "O portal está sobrecarregado e as caixas de data não apareceram. "
                "Vou esperar e tentar de novo (%s/6)." % tentativa
            )
            _esperar_se_pesado(page, log)
        else:
            log(
                "Caixas de data ainda não apareceram (%s/6). "
                "O portal pode estar lento ou sobrecarregado; vou atualizar a página." % tentativa
            )
            page.wait_for_timeout(10000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
    if campo_ini is None or campo_fim is None:
        _salvar_evidencia(page, log, "campos de data não encontrados")
        raise RuntimeError(
            "Cheguei na tela, mas não achei as caixas Data inicial / Data final do portal "
            "para colar o período que você escolheu no Coletor. O portal pode estar sobrecarregado. "
            "Página atual: " + page.url
        )
    falha_data = None
    for tentativa_data in range(1, 4):
        try:
            _preencher_data(campo_ini, ini_s)
            _preencher_data(campo_fim, fim_s)
            falha_data = None
            break
        except Exception as e:
            falha_data = e
            log(
                "A barra do portal cobriu a caixa de data. Vou tentar de novo (%s/3)."
                % tentativa_data
            )
            try:
                page.evaluate("() => window.scrollTo(0, 320)")
            except Exception:
                pass
            page.wait_for_timeout(600)
            campo_ini = _localizar_data(page, "inicio") or campo_ini
            campo_fim = _localizar_data(page, "fim") or campo_fim
    if falha_data is not None:
        base = URL_RECEBIDAS.split("?")[0]
        if "Notas/Emitidas" not in base and "Notas/Recebidas" not in base:
            atual = page.url.split("?")[0]
            if "Notas/Emitidas" in atual or "Notas/Recebidas" in atual:
                base = atual
        endereco = (
            base
            + "?executar=1&busca=&datainicio="
            + ini_s
            + "&datafim="
            + fim_s
        )
        log("As caixas de data não aceitaram o período. Vou abrir esse período direto no endereço.")
        try:
            page.goto(endereco, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        if "datainicio=" not in page.url.lower() and _localizar_data(page, "inicio") is None:
            _salvar_evidencia(page, log, "não consegui preencher a data")
            raise RuntimeError(
                "Não consegui preencher as datas do período. Página atual: " + page.url
            )
        _esperar_se_pesado(page, log)
        return
    clicou = False
    for tentativa_filtro in range(1, 4):
        candidatos = [
            page.get_by_role("button", name=RE_FILTRAR),
            page.locator("button, a.btn, input[type=submit], input[type=button]").filter(
                has_text=re.compile(r"^\s*Filtrar", re.I)
            ),
        ]
        for loc in candidatos:
            try:
                if loc.count() == 0:
                    continue
                alvo = loc.first
                try:
                    alvo.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest'})")
                except Exception:
                    pass
                alvo.click(force=True, timeout=8000)
                clicou = True
                break
            except Exception:
                continue
        if clicou:
            break
        try:
            clicou = bool(
                page.evaluate(
                    """() => {
                        const els = [...document.querySelectorAll('button, a.btn, input[type=submit], input[type=button]')];
                        const visivel = (el) => {
                            const r = el.getBoundingClientRect();
                            const s = getComputedStyle(el);
                            return r.width > 8 && r.height > 8 && s.visibility !== 'hidden' && s.display !== 'none';
                        };
                        const texto = (el) => ((el.innerText || el.value || '') + '').replace(/\\s+/g, ' ').trim();
                        const alvo = els.find((el) => /^filtrar$/i.test(texto(el)) && visivel(el))
                            || els.find((el) => /filtrar/i.test(texto(el)) && visivel(el));
                        if (!alvo) return false;
                        alvo.scrollIntoView({block: 'center'});
                        alvo.click();
                        return true;
                    }"""
                )
            )
        except Exception:
            clicou = False
        if clicou:
            break
        log("O botão Filtrar não respondeu. Vou tentar de novo (%s/3)." % tentativa_filtro)
        page.wait_for_timeout(800)
    if not clicou:
        _salvar_evidencia(page, log, "não consegui clicar em Filtrar")
        raise RuntimeError("Não consegui clicar em Filtrar. Página atual: " + page.url)
    _pausa_humana(page, 1100, 1800)
    _esperar_se_pesado(page, log)


def _preencher_data(loc, valor):
    try:
        loc.evaluate("el => el.scrollIntoView({block: 'center', inline: 'nearest'})")
    except Exception:
        pass
    try:
        loc.page.wait_for_timeout(200)
    except Exception:
        pass
    try:
        loc.click(force=True, timeout=8000)
        loc.page.wait_for_timeout(random.randint(80, 180))
        loc.press("Control+A")
        loc.press("Backspace")
        loc.type(valor, delay=random.randint(35, 70))
        loc.press("Tab")
    except Exception:
        loc.evaluate(
            """(el, valor) => {
                el.focus();
                el.value = valor;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
            }""",
            valor,
        )


def _ir_para_recebidas(page, log):
    destino = URL_RECEBIDAS
    emitidas = "emitid" in destino.lower()
    marcador = "emitidas" if emitidas else "recebidas"
    trecho = "Notas/Emitidas" if emitidas else "Notas/Recebidas"
    nome = "NFS-e Emitidas" if emitidas else "NFS-e Recebidas"

    def chegou():
        bruto = (page.url or "").lower()
        if "visualizar" in bruto:
            return False
        return marcador in bruto

    def esperar_abrir(segundos):
        fim = time.time() + segundos
        while time.time() < fim:
            if chegou():
                return True
            page.wait_for_timeout(500)
        return chegou()

    page = _pagina_do_painel(page.context, page)
    try:
        page.bring_to_front()
    except Exception:
        pass
    if chegou():
        return page
    if "/dashboard" in _url_norm(page):
        log(
            "Estou no Dashboard. As notas %s não ficam nessa tela — vou abrir %s."
            % ("emitidas" if emitidas else "recebidas", nome)
        )
        if _clicar_icone_recebidas(page, log) or esperar_abrir(8):
            log("Tela de %s aberta." % nome)
            return page
    log("Abrindo %s: %s" % (nome, destino))
    try:
        page.goto(destino, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        log("Não abri pelo endereço (%s). Vou conferir se a tela abriu mesmo assim…" % e)
        if esperar_abrir(12):
            log("Tela de %s aberta, apesar do aviso do Chrome." % nome)
            return page
        if _clicar_icone_recebidas(page, log) or esperar_abrir(8):
            log("Tela de %s aberta." % nome)
            return page
        try:
            page.goto(destino, wait_until="commit", timeout=60000)
        except Exception:
            pass
        if esperar_abrir(12):
            log("Tela de %s aberta." % nome)
            return page
        _salvar_evidencia(page, log, "falha ao abrir " + nome)
        raise RuntimeError("Não abri a tela de %s: %s" % (nome, e)) from e
    try:
        page.wait_for_url(re.compile(trecho, re.I), timeout=15000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(800)
    _esperar_se_pesado(page, log)
    if chegou():
        log("Tela de %s aberta." % nome)
        return page
    if _na_tela_login(page):
        _salvar_evidencia(page, log, "voltou para o login")
        raise RuntimeError(
            "Voltei para o login ao abrir %s. Faça o login de novo no Chrome." % nome
        )
    if "/dashboard" in _url_norm(page):
        log("Ainda no Dashboard; tentando o ícone e o endereço de novo…")
        if _clicar_icone_recebidas(page, log) or esperar_abrir(8):
            log("Tela de %s aberta." % nome)
            return page
        try:
            page.goto(destino, wait_until="commit", timeout=60000)
        except Exception:
            pass
        page.wait_for_timeout(1000)
        if chegou():
            log("Tela de %s aberta." % nome)
            return page
    for _ in range(12):
        if marcador in (page.url or "").lower():
            log("Campos de data encontrados — seguindo.")
            return page
        page.wait_for_timeout(400)
    _salvar_evidencia(page, log, "não chegou em " + nome)
    raise RuntimeError(
        "Não cheguei em %s. Página atual: %s" % (destino, page.url)
    )
