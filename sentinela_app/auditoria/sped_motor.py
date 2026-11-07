from decimal import Decimal, InvalidOperation

from clientes.models import DocumentoXML

_TOLERANCIA = Decimal("0.05")


def _decimal(texto: str) -> Decimal:
    bruto = (texto or "").strip()
    if not bruto:
        return Decimal("0.00")
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    try:
        return Decimal(bruto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


class MotorSpedCruzamento:
    def __init__(self, empresa_obj, competencia_mmaaaa: str, caminho_arquivo_sped: str):
        self.empresa = empresa_obj
        self.competencia = competencia_mmaaaa
        self.caminho_sped = caminho_arquivo_sped

    def parse_sped_c100(self):
        notas_sped = {}
        abertura = {}
        with open(self.caminho_sped, "r", encoding="latin-1") as arquivo:
            for linha in arquivo:
                if not linha.startswith("|"):
                    continue
                campos = linha.strip().split("|")
                registro = campos[1] if len(campos) > 1 else ""
                if registro == "0000":
                    abertura = {
                        "inicio": campos[4] if len(campos) > 4 else "",
                        "cnpj": "".join(caractere for caractere in (campos[7] if len(campos) > 7 else "") if caractere.isdigit()),
                    }
                elif registro == "C100" and len(campos) > 12:
                    chave = "".join(caractere for caractere in campos[9] if caractere.isdigit())
                    if len(chave) != 44:
                        continue
                    notas_sped[chave] = {
                        "ind_oper": campos[2] if len(campos) > 2 else "",
                        "cod_mod": campos[5] if len(campos) > 5 else "",
                        "num_doc": campos[8] if len(campos) > 8 else "",
                        "valor_total": _decimal(campos[12]),
                    }
        return abertura, notas_sped

    def executar_cruzamento(self):
        try:
            abertura, mapa_sped = self.parse_sped_c100()
        except OSError as erro:
            return {"erro": f"Não foi possível ler o SPED: {erro}"}
        if not abertura:
            return {"erro": "O arquivo não tem o registro 0000 do SPED Fiscal."}
        if abertura["cnpj"] and abertura["cnpj"] != self.empresa.cnpj:
            return {"erro": "O CNPJ do SPED é de outra empresa."}
        inicio = abertura.get("inicio", "")
        if len(inicio) == 8 and inicio.isdigit():
            competencia_sped = f"{inicio[2:4]}{inicio[4:]}"
            if competencia_sped != self.competencia:
                return {"erro": f"O SPED é da competência {competencia_sped} e a sessão está em {self.competencia}."}

        documentos = list(DocumentoXML.objects.filter(empresa=self.empresa, competencia=self.competencia))
        DocumentoXML.objects.filter(empresa=self.empresa, competencia=self.competencia).update(
            possui_divergencia=False,
            mensagem_divergencia="",
            sugestao_correcao="",
        )
        mapa_xmls = {doc.chave_acesso: doc for doc in documentos}
        chaves_xml = set(mapa_xmls)
        chaves_sped = set(mapa_sped)
        faltando_no_sped = chaves_xml - chaves_sped
        sem_xml = chaves_sped - chaves_xml
        em_comum = chaves_xml & chaves_sped

        divergencias_valor = []
        for chave in em_comum:
            doc = mapa_xmls[chave]
            diferenca = abs(doc.valor_total - mapa_sped[chave]["valor_total"])
            if diferenca > _TOLERANCIA:
                divergencias_valor.append({
                    "chave": chave,
                    "numero_nota": doc.numero_nota,
                    "valor_xml": str(doc.valor_total),
                    "valor_sped": str(mapa_sped[chave]["valor_total"]),
                    "diferenca": str(diferenca.quantize(Decimal("0.01"))),
                })

        for chave in faltando_no_sped:
            doc = mapa_xmls[chave]
            doc.possui_divergencia = True
            doc.mensagem_divergencia = "Nota presente nos XMLs (SEFAZ) mas AUSENTE no SPED Fiscal da Domínio."
            doc.sugestao_correcao = "Lançar nota faltante na Domínio para evitar omissão."
            doc.save(update_fields=["possui_divergencia", "mensagem_divergencia", "sugestao_correcao"])

        chaves_valor = {item["chave"] for item in divergencias_valor}
        for chave in em_comum:
            if chave not in chaves_valor:
                continue
            doc = mapa_xmls[chave]
            doc.possui_divergencia = True
            doc.mensagem_divergencia = "Divergência no Valor Total entre o XML e o SPED da Domínio."
            doc.sugestao_correcao = "Verificar acumulador ou redutores de base na Domínio."
            doc.save(update_fields=["possui_divergencia", "mensagem_divergencia", "sugestao_correcao"])

        return {
            "erro": "",
            "total_xmls_lidos": len(chaves_xml),
            "total_notas_sped": len(chaves_sped),
            "faltando_no_sped": [
                {"chave": chave, "nota": mapa_xmls[chave].numero_nota, "valor": str(mapa_xmls[chave].valor_total)}
                for chave in faltando_no_sped
            ],
            "sem_xml_no_sped": [
                {"chave": chave, "numero": mapa_sped[chave]["num_doc"], "valor": str(mapa_sped[chave]["valor_total"])}
                for chave in sem_xml
            ],
            "divergencias_valor": divergencias_valor,
        }
