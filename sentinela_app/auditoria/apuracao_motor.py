from decimal import Decimal

from clientes.models import DocumentoXML


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
        return {"pis": pis, "cofins": cofins, "regime": regime, "explicacao": explicacao}

    def calcular_difal_consumidor_final(self):
        """A partilha do DIFAL depende da UF de destino e fica para a próxima leitura das tags de partilha."""
        return []
