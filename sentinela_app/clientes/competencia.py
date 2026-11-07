from django.utils import timezone

MESES = ("Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez")


def competencia_atual() -> str:
    hoje = timezone.localdate()
    return f"{hoje.month:02d}{hoje.year}"


def interpretar(valor: str) -> tuple[int, int] | None:
    if len(valor) == 6 and valor.isdigit():
        mes, ano = int(valor[:2]), int(valor[2:])
        if 1 <= mes <= 12 and 2000 <= ano <= 2100:
            return mes, ano
    return None


def rotulo(valor: str) -> str:
    partes = interpretar(valor)
    if partes is None:
        return valor
    mes, ano = partes
    return f"{MESES[mes - 1]}/{ano}"


def montar(mes: int, ano: int) -> str:
    return f"{mes:02d}{ano}"


def deslocar(valor: str, delta: int) -> str:
    partes = interpretar(valor) or (timezone.localdate().month, timezone.localdate().year)
    indice = partes[1] * 12 + (partes[0] - 1) + delta
    return montar((indice % 12) + 1, indice // 12)
