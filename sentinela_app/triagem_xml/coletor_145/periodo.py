from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta


MESES = [
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
]

# O portal recusa intervalo maior que 30 dias (inclusivo).
MAX_DIAS_INCLUSIVOS = 30


@dataclass(frozen=True)
class Periodo:
    inicio: date
    fim: date
    rotulo: str

    def fatias(self) -> list[tuple[date, date]]:
        partes: list[tuple[date, date]] = []
        atual = self.inicio
        while atual <= self.fim:
            ultimo = min(atual + timedelta(days=MAX_DIAS_INCLUSIVOS - 1), self.fim)
            partes.append((atual, ultimo))
            atual = ultimo + timedelta(days=1)
        return partes


def mes_para_numero(nome: str) -> int:
    chave = (nome or "").strip().lower()
    for i, m in enumerate(MESES, start=1):
        if m.lower() == chave:
            return i
    raise ValueError(f"Mês inválido: {nome}")


def periodo_do_mes(mes: int, ano: int) -> Periodo:
    ultimo = calendar.monthrange(ano, mes)[1]
    inicio = date(ano, mes, 1)
    fim = date(ano, mes, ultimo)
    return Periodo(inicio=inicio, fim=fim, rotulo=f"{MESES[mes - 1]} de {ano}")


def parse_data_br(texto: str) -> date:
    bruto = (texto or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(bruto, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Data inválida: {texto}")


def periodo_livre(data_inicial: str, data_final: str) -> Periodo:
    inicio = parse_data_br(data_inicial)
    fim = parse_data_br(data_final)
    if fim < inicio:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    return Periodo(
        inicio=inicio,
        fim=fim,
        rotulo=f"{inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}",
    )


def fmt_br(d: date) -> str:
    return d.strftime("%d/%m/%Y")
