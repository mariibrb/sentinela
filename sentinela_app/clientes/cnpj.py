def somente_digitos(texto: str) -> str:
    return "".join(caractere for caractere in str(texto or "") if caractere.isdigit())


def digitos_conferem(cnpj: str) -> bool:
    numero = somente_digitos(cnpj)
    if len(numero) != 14 or numero == numero[0] * 14:
        return False

    def calcular(base, pesos):
        resto = sum(int(digito) * peso for digito, peso in zip(base, pesos)) % 11
        return "0" if resto < 2 else str(11 - resto)

    primeiro = calcular(numero[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    segundo = calcular(numero[:12] + primeiro, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    return numero[-2:] == primeiro + segundo
