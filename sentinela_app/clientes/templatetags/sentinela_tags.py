from decimal import Decimal

from django import template

from clientes.cnpj import somente_digitos

register = template.Library()


@register.filter
def brl(valor):
    quantizado = Decimal(valor or 0).quantize(Decimal("0.01"))
    texto = f"{quantizado:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter
def cnpj(valor):
    digitos = somente_digitos(valor)
    if len(digitos) != 14:
        return valor
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
