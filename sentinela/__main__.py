"""Triagem de uma pasta de XML: python -m sentinela CNPJ origem destino."""

import sys

from sentinela.triagem import SentinelaMotorTriagem


def main() -> int:
    if len(sys.argv) != 4:
        print("Uso: python -m sentinela CNPJ pasta_xml pasta_destino")
        return 1
    _, cnpj, origem, destino = sys.argv
    resumo = SentinelaMotorTriagem(cnpj).processar_pasta(origem, destino)
    print(f"Copiados: {resumo['copiados']}. Ignorados: {resumo['ignorados']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
