from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from clientes.carteira_fiscal import importar_carteira_fiscal


class Command(BaseCommand):
    help = (
        "Importa a planilha Fiscal (aba EMPRESAS) usando só CÓD, CNPJ, "
        "RESPONSAVEL (executa) e REVISADO."
    )

    def add_arguments(self, parser):
        parser.add_argument("caminho", type=str, help="Caminho do arquivo .xlsx")
        parser.add_argument(
            "--sem-cnpj",
            action="store_true",
            help="Não consulta a API de CNPJ (só atualiza carteira; novos ficam com stub).",
        )

    def handle(self, *args, **options):
        caminho = Path(options["caminho"])
        if not caminho.is_file():
            raise CommandError(f"Arquivo não encontrado: {caminho}")
        self.stdout.write(f"Lendo {caminho}…")
        resultado = importar_carteira_fiscal(
            caminho.read_bytes(),
            buscar_cnpj=not options["sem_cnpj"],
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Criados: {resultado['criados']} | Atualizados: {resultado['atualizados']}"
            )
        )
        for erro in resultado.get("erros") or []:
            self.stdout.write(self.style.ERROR(erro))
        if not resultado.get("ok"):
            raise CommandError("Importação da carteira Fiscal terminou com falhas.")
