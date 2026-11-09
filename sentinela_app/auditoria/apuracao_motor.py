from decimal import Decimal

from clientes.competencia import deslocar, rotulo
from clientes.models import DocumentoXML, NotaServico


class MotorApuracaoSentinela:
    def __init__(self, empresa_obj, competencia_mmaaaa: str):
        self.empresa = empresa_obj
        self.competencia = competencia_mmaaaa

    def _autorizadas(self):
        return DocumentoXML.objects.filter(
            empresa=self.empresa,
            competencia=self.competencia,
            modelo="55",
            status_sefa="AUTORIZADA",
        )

    def _nfse_da_competencia(self):
        return NotaServico.objects.filter(
            empresa=self.empresa,
            competencia=self.competencia,
        )

    def calcular_apuracao_icms(self):
        total_debitos_icms = Decimal("0.00")
        total_creditos_icms = Decimal("0.00")
        total_icms_st = Decimal("0.00")
        notas_com_alerta = []
        for doc in self._autorizadas():
            if doc.tipo_operacao == "SAIDA_PROPRIA":
                total_debitos_icms += doc.valor_icms
                total_icms_st += doc.valor_icms_st
                if doc.base_icms > 0 and doc.valor_icms == 0:
                    notas_com_alerta.append({
                        "chave": doc.chave_acesso,
                        "numero": doc.numero_nota,
                        "tipo": "ERRO DE EMISSÃO",
                        "descricao": f"Nota de saída com Base de ICMS (R$ {doc.base_icms}) mas Valor de ICMS Zerado.",
                        "sugestao": "Emitir Nota Fiscal Complementar de ICMS ou verificar CST.",
                    })
            elif doc.tipo_operacao in {"ENTRADA_TERCEIROS", "ENTRADA_EMISSAO_PROPRIA"}:
                total_creditos_icms += doc.valor_icms
        return {
            "total_debitos": total_debitos_icms,
            "total_creditos": total_creditos_icms,
            "saldo_icms": total_debitos_icms - total_creditos_icms,
            "total_icms_st": total_icms_st,
            "alertas_encontrados": notas_com_alerta,
        }

    def calcular_apuracao_nfse(self):
        notas = list(self._nfse_da_competencia())
        vindas_mes_anterior = list(
            NotaServico.objects.filter(
                empresa=self.empresa,
                competencia_escrituracao=self.competencia,
                etapa_escrituracao="MES_SEGUINTE",
            ).exclude(competencia=self.competencia)
        )

        prestados_ativos = []
        prestados_cancelados = []
        tomados_ativos = []
        tomados_cancelados = []

        ficarao_mes_seguinte = []
        pendentes_escriturar = []
        escrituradas_mes = []
        alertas_nfse = []

        for n in notas:
            cancelada = (n.status or "").strip().lower() == "cancelada" or n.etapa_escrituracao == "NAO_ESCRITURAR"
            if n.possui_alerta:
                alertas_nfse.append(n)
            if cancelada:
                if n.tipo == "PRESTADO":
                    prestados_cancelados.append(n)
                else:
                    tomados_cancelados.append(n)
                continue

            if n.etapa_escrituracao == "MES_SEGUINTE":
                ficarao_mes_seguinte.append(n)
            elif n.etapa_escrituracao == "ESCRITURADA":
                escrituradas_mes.append(n)
            else:
                pendentes_escriturar.append(n)

            # Notas que entram na apuração deste mês (exclui as que ficaram para o mês seguinte)
            if n.etapa_escrituracao != "MES_SEGUINTE":
                if n.tipo == "PRESTADO":
                    prestados_ativos.append(n)
                else:
                    tomados_ativos.append(n)

        # Somar também as notas vindas do mês anterior para escriturar neste mês
        for n in vindas_mes_anterior:
            if (n.status or "").strip().lower() != "cancelada":
                if n.tipo == "PRESTADO":
                    prestados_ativos.append(n)
                else:
                    tomados_ativos.append(n)

        def _iss_eh_retido(nota) -> bool:
            txt = (nota.retencao or "").lower()
            return ("retid" in txt and "não" not in txt and "nao" not in txt) and nota.valor_issqn > 0

        # Totais de Serviços Prestados
        fat_servicos = sum((n.valor_servico for n in prestados_ativos), Decimal("0.00"))
        liq_prestados = sum((n.valor_liquido for n in prestados_ativos), Decimal("0.00"))
        iss_retido_sofrido = sum((n.valor_issqn for n in prestados_ativos if _iss_eh_retido(n)), Decimal("0.00"))
        iss_proprio = sum((n.valor_issqn for n in prestados_ativos if not _iss_eh_retido(n)), Decimal("0.00"))
        csrf_sofrido = sum((n.contrib_sociais_retidas for n in prestados_ativos), Decimal("0.00"))
        inss_sofrido = sum((n.contrib_previdenciaria_retida for n in prestados_ativos), Decimal("0.00"))
        irrf_sofrido = sum((n.irrf for n in prestados_ativos), Decimal("0.00"))

        # Totais de Serviços Tomados
        tot_tomados = sum((n.valor_servico for n in tomados_ativos), Decimal("0.00"))
        liq_tomados = sum((n.valor_liquido for n in tomados_ativos), Decimal("0.00"))
        iss_retido_recolher = sum((n.valor_issqn for n in tomados_ativos if _iss_eh_retido(n)), Decimal("0.00"))
        csrf_recolher = sum((n.contrib_sociais_retidas for n in tomados_ativos), Decimal("0.00"))
        inss_recolher = sum((n.contrib_previdenciaria_retida for n in tomados_ativos), Decimal("0.00"))
        irrf_recolher = sum((n.irrf for n in tomados_ativos), Decimal("0.00"))
        total_guias_tomados = iss_retido_recolher + csrf_recolher + inss_recolher + irrf_recolher

        # Verificar salto de numeração (buracos) nas NFS-e Prestadas
        numeros_prestados = sorted({
            int(str(n.numero).lstrip("0") or "0")
            for n in notas
            if n.tipo == "PRESTADO" and str(n.numero).strip().isdigit() and int(str(n.numero).strip()) > 0
        })
        buracos_nfse = []
        for ant, seg in zip(numeros_prestados, numeros_prestados[1:]):
            qtd = seg - ant - 1
            if 0 < qtd <= 500:
                faixa = str(ant + 1) if qtd == 1 else f"{ant + 1} a {seg - 1}"
                buracos_nfse.append({"faixa": faixa, "quantidade": qtd})

        prox_comp = deslocar(self.competencia, 1)
        ant_comp = deslocar(self.competencia, -1)

        return {
            "total_notas": len(notas),
            "prestados": {
                "qtd_ativas": len(prestados_ativos),
                "qtd_canceladas": len(prestados_cancelados),
                "valor_servico": fat_servicos,
                "valor_liquido": liq_prestados,
                "iss_proprio": iss_proprio,
                "iss_retido": iss_retido_sofrido,
                "csrf_retido": csrf_sofrido,
                "inss_retido": inss_sofrido,
                "irrf_retido": irrf_sofrido,
            },
            "tomados": {
                "qtd_ativas": len(tomados_ativos),
                "qtd_canceladas": len(tomados_cancelados),
                "valor_servico": tot_tomados,
                "valor_liquido": liq_tomados,
                "iss_recolher": iss_retido_recolher,
                "csrf_recolher": csrf_recolher,
                "inss_recolher": inss_recolher,
                "irrf_recolher": irrf_recolher,
                "total_guias_recolher": total_guias_tomados,
            },
            "escrituracao": {
                "pendentes": pendentes_escriturar,
                "qtd_pendentes": len(pendentes_escriturar),
                "valor_pendentes": sum((n.valor_servico for n in pendentes_escriturar), Decimal("0.00")),
                "escrituradas": escrituradas_mes,
                "qtd_escrituradas": len(escrituradas_mes),
                "valor_escrituradas": sum((n.valor_servico for n in escrituradas_mes), Decimal("0.00")),
                "mes_seguinte": ficarao_mes_seguinte,
                "qtd_mes_seguinte": len(ficarao_mes_seguinte),
                "valor_mes_seguinte": sum((n.valor_servico for n in ficarao_mes_seguinte), Decimal("0.00")),
                "rotulo_mes_seguinte": rotulo(prox_comp),
                "vindas_mes_anterior": vindas_mes_anterior,
                "qtd_vindas_anterior": len(vindas_mes_anterior),
                "valor_vindas_anterior": sum((n.valor_servico for n in vindas_mes_anterior), Decimal("0.00")),
                "rotulo_mes_anterior": rotulo(ant_comp),
            },
            "buracos_prestados": buracos_nfse,
            "alertas": alertas_nfse,
        }

    def calcular_pis_cofins(self):
        regime = (self.empresa.regime_pis_cofins or "").strip()
        debito_pis = Decimal("0.00")
        debito_cofins = Decimal("0.00")
        credito_pis = Decimal("0.00")
        credito_cofins = Decimal("0.00")
        for doc in self._autorizadas():
            if doc.tipo_operacao == "SAIDA_PROPRIA":
                debito_pis += doc.valor_pis
                debito_cofins += doc.valor_cofins
            elif doc.tipo_operacao in {"ENTRADA_TERCEIROS", "ENTRADA_EMISSAO_PROPRIA"}:
                credito_pis += doc.valor_pis
                credito_cofins += doc.valor_cofins

        nfse_info = self.calcular_apuracao_nfse()
        faturamento_servicos = nfse_info["prestados"]["valor_servico"]
        csrf_retido = nfse_info["prestados"]["csrf_retido"]

        if regime == "Não-Cumulativo":
            pis = debito_pis - credito_pis
            cofins = debito_cofins - credito_cofins
            explicacao = "Não-cumulativo: débitos das saídas próprias menos créditos das entradas."
        elif regime == "Cumulativo":
            pis = debito_pis
            cofins = debito_cofins
            explicacao = "Cumulativo: a apuração usa os débitos das saídas próprias. Entradas não geram crédito."
        else:
            pis = Decimal("0.00")
            cofins = Decimal("0.00")
            anexo = (self.empresa.anexo_simples or "").strip()
            complemento = f" Anexo {anexo}." if anexo else ""
            explicacao = f"Simples Nacional: PIS e COFINS seguem o anexo e ficam no DAS.{complemento}"
        return {
            "pis": pis,
            "cofins": cofins,
            "regime": regime,
            "explicacao": explicacao,
            "faturamento_servicos": faturamento_servicos,
            "csrf_retido_servicos": csrf_retido,
        }

    def resumo_fechamento_completo(self, sped_ausentes=None):
        """Reúne todas as etapas do mês (XML NF-e/NFC-e, SPED C100, Coletor NFS-e 1.4.5 e Corte de Competência)."""
        icms = self.calcular_apuracao_icms()
        pis_cofins = self.calcular_pis_cofins()
        nfse = self.calcular_apuracao_nfse()

        docs_xml = DocumentoXML.objects.filter(empresa=self.empresa, competencia=self.competencia)
        total_xml = docs_xml.count()
        divergencias_xml = docs_xml.filter(possui_divergencia=True).count()
        ausentes_sped = len(sped_ausentes or [])

        etapas = [
            {
                "numero": 1,
                "titulo": "Triagem e Garimpeiro de XML (NF-e / NFC-e)",
                "status": "OK" if total_xml > 0 else "PENDENTE",
                "resumo": (
                    f"{total_xml} documento(s) XML lido(s) nesta competência."
                    if total_xml > 0
                    else "Nenhum lote de NF-e/NFC-e lido nesta competência (ou empresa apenas de serviços)."
                ),
            },
            {
                "numero": 2,
                "titulo": "Apanhador de NFS-e v1.4.5 (Prestados e Tomados)",
                "status": (
                    "ATENCAO"
                    if (nfse["buracos_prestados"] or len(nfse["alertas"]) > 0)
                    else ("OK" if nfse["total_notas"] > 0 else "PENDENTE")
                ),
                "resumo": (
                    f"Prestadas: {nfse['prestados']['qtd_ativas']} ativa(s) (R$ {nfse['prestados']['valor_servico']}) · "
                    f"Tomadas: {nfse['tomados']['qtd_ativas']} ativa(s) (R$ {nfse['tomados']['valor_servico']})."
                    if nfse["total_notas"] > 0
                    else "Apanhador NFS-e ainda não executado ou sem notas nesta competência."
                ),
            },
            {
                "numero": 3,
                "titulo": "Conferência de Escrituração e Corte para o Mês Seguinte",
                "status": (
                    "ATENCAO"
                    if nfse["escrituracao"]["qtd_pendentes"] > 0 or nfse["escrituracao"]["qtd_mes_seguinte"] > 0
                    else ("OK" if nfse["total_notas"] > 0 else "PENDENTE")
                ),
                "resumo": (
                    f"Falta escriturar/conferir: {nfse['escrituracao']['qtd_pendentes']} nota(s) · "
                    f"Já escrituradas: {nfse['escrituracao']['qtd_escrituradas']} · "
                    f"Ficarão para {nfse['escrituracao']['rotulo_mes_seguinte']}: {nfse['escrituracao']['qtd_mes_seguinte']} nota(s)."
                ),
            },
            {
                "numero": 4,
                "titulo": "Cruzamento SPED Fiscal e Auditoria de Divergências",
                "status": (
                    "ERRO"
                    if (divergencias_xml > 0 or ausentes_sped > 0 or icms["alertas_encontrados"])
                    else ("OK" if total_xml > 0 else "PENDENTE")
                ),
                "resumo": (
                    f"{divergencias_xml} divergência(s) no XML, {ausentes_sped} nota(s) ausente(s) no XML e "
                    f"{len(icms['alertas_encontrados'])} alerta(s) de emissão de ICMS."
                ),
            },
        ]

        return {
            "icms": icms,
            "pis_cofins": pis_cofins,
            "nfse": nfse,
            "etapas": etapas,
            "total_xml": total_xml,
            "divergencias_xml": divergencias_xml,
            "ausentes_sped": ausentes_sped,
        }

    def calcular_difal_consumidor_final(self):
        """A partilha do DIFAL depende da UF de destino e fica para a próxima leitura das tags de partilha."""
        return []
