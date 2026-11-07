from decimal import Decimal, InvalidOperation

from clientes.models import DocumentoXML

_REGULAR = {"00", "01", "06", "07", "08"}
_CANCELADA = {"02", "03"}
_DENEGADA = {"04"}
_TOLERANCIA = Decimal("0.05")


def _decimal_sped(texto: str) -> Decimal:
    bruto = (texto or "").strip()
    if not bruto:
        return Decimal("0.00")
    if "," in bruto:
        bruto = bruto.replace(".", "").replace(",", ".")
    try:
        return Decimal(bruto).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0.00")


def _ler_sped(conteudo: bytes) -> tuple[dict, list[dict]]:
    texto = conteudo.decode("utf-8-sig")
    if "\x00" in texto or "|0000|" not in texto:
        texto = conteudo.decode("cp1252")
    abertura = {}
    notas = []
    for linha in texto.splitlines():
        if not linha.startswith("|"):
            continue
        campos = linha.split("|")
        registro = campos[1] if len(campos) > 1 else ""
        if registro == "0000":
            abertura = {"inicio": campos[4] if len(campos) > 4 else "", "cnpj": campos[7] if len(campos) > 7 else ""}
        elif registro == "C100" and len(campos) > 12:
            chave = "".join(caractere for caractere in campos[9] if caractere.isdigit())
            if len(chave) != 44:
                continue
            notas.append({
                "chave": chave,
                "situacao": campos[6].strip(),
                "modelo": campos[5].strip(),
                "serie": campos[7].strip(),
                "numero": campos[8].strip(),
                "valor": _decimal_sped(campos[12]),
            })
    return abertura, notas


def _situacao_sped(codigo: str) -> str:
    if codigo in _CANCELADA:
        return "CANCELADA"
    if codigo in _DENEGADA:
        return "DENEGADA"
    if codigo in _REGULAR:
        return "AUTORIZADA"
    return "OUTRA"


def cruzar_sped(empresa, competencia: str, conteudo: bytes) -> dict:
    abertura, notas = _ler_sped(conteudo)
    cnpj = "".join(caractere for caractere in abertura.get("cnpj", "") if caractere.isdigit())
    if cnpj and cnpj != empresa.cnpj:
        return {"erro": "O CNPJ do SPED é de outra empresa."}
    inicio = abertura.get("inicio", "")
    if len(inicio) == 8 and inicio.isdigit():
        competencia_sped = f"{inicio[2:4]}{inicio[4:]}"
        if competencia_sped != competencia:
            return {"erro": f"O SPED é da competência {competencia_sped} e a sessão está em {competencia}."}

    por_chave = {nota["chave"]: nota for nota in notas}
    documentos = list(DocumentoXML.objects.filter(empresa=empresa, competencia=competencia))
    DocumentoXML.objects.filter(empresa=empresa, competencia=competencia).update(
        possui_divergencia=False, mensagem_divergencia="", sugestao_correcao=""
    )
    chaves_xml = set()
    for documento in documentos:
        chaves_xml.add(documento.chave_acesso)
        sped = por_chave.get(documento.chave_acesso)
        if sped is None:
            documento.possui_divergencia = True
            documento.mensagem_divergencia = "A nota está no XML e não aparece no C100 deste SPED."
            documento.sugestao_correcao = "Carta de Correção"
            documento.save(update_fields=["possui_divergencia", "mensagem_divergencia", "sugestao_correcao"])
            continue
        situacao = _situacao_sped(sped["situacao"])
        if situacao != "OUTRA" and situacao != documento.status_sefa and not (
            situacao == "AUTORIZADA" and documento.status_sefa == "AUTORIZADA"
        ):
            if documento.status_sefa != situacao:
                documento.possui_divergencia = True
                documento.mensagem_divergencia = f"XML {documento.status_sefa} e SPED com situação {sped['situacao']}."
                documento.sugestao_correcao = "Ajustar a situação no SPED"
                documento.save(update_fields=["possui_divergencia", "mensagem_divergencia", "sugestao_correcao"])
                continue
        if documento.status_sefa == "AUTORIZADA" and situacao == "AUTORIZADA" and abs(documento.valor_total - sped["valor"]) > _TOLERANCIA:
            documento.possui_divergencia = True
            documento.mensagem_divergencia = f"Valor do XML {documento.valor_total} e valor do C100 {sped['valor']}."
            documento.sugestao_correcao = "Nota Complementar"
            documento.save(update_fields=["possui_divergencia", "mensagem_divergencia", "sugestao_correcao"])

    ausentes = [nota for nota in notas if nota["chave"] not in chaves_xml]
    return {"erro": "", "ausentes": ausentes, "notas_sped": len(notas), "notas_xml": len(documentos)}
