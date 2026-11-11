from django.core.management.base import BaseCommand, CommandError

from fechamento.sync_manual import sincronizar_do_manual


class Command(BaseCommand):
    help = (
        "Lê a pasta Manual do OneDrive (somente leitura) e atualiza as cópias "
        "embutidas do Fechamento dentro do Sentinela. Nunca altera a pasta fonte."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fonte",
            default="",
            help="Caminho da pasta Manual (opcional; padrão = SENTINELA_FECHAMENTO_SRC).",
        )

    def handle(self, *args, **options):
        fonte = (options.get("fonte") or "").strip() or None
        try:
            resultado = sincronizar_do_manual(fonte=fonte)
        except Exception as erro:
            raise CommandError(str(erro)) from erro
        self.stdout.write(self.style.SUCCESS("Sincronização concluída (fonte intacta)."))
        self.stdout.write(f"Fonte lida: {resultado['fonte']}")
        self.stdout.write(f"JSON Sentinela: {resultado['json']}")
        self.stdout.write(f"Regimes: {', '.join(resultado['regimes'])} ({resultado['itens']} itens)")
        self.stdout.write(f"Assets: {', '.join(resultado['assets']) or '(nenhum)'}")
