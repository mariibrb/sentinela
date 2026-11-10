import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from clientes.models import Colaborador


class Command(BaseCommand):
    help = "Garante usuários iniciais configurados por variáveis de ambiente no deploy em nuvem."

    def handle(self, *args, **options):
        User = get_user_model()
        admin_user = os.getenv("DJANGO_SUPERUSER_USERNAME", "desenvolvedora").strip()
        admin_pass = os.getenv("DJANGO_SUPERUSER_PASSWORD", "").strip()
        admin_email = os.getenv("DJANGO_SUPERUSER_EMAIL", "").strip()

        if admin_user and admin_pass:
            usuario, criado = User.objects.get_or_create(
                username=admin_user,
                defaults={
                    "email": admin_email,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            if criado or not usuario.has_usable_password():
                usuario.is_staff = True
                usuario.is_superuser = True
                usuario.set_password(admin_pass)
                usuario.save()

        self._garantir_analista(
            User,
            os.getenv("SENTINELA_COLABORADOR_USER", "mariana.barbosa").strip(),
            os.getenv("SENTINELA_COLABORADOR_PASSWORD", "").strip(),
            os.getenv("SENTINELA_COLABORADOR_EMAIL", "mariana.barbosa@nascel.com.br").strip(),
            "Mariana",
            "Barbosa",
        )
        self._garantir_analista(
            User,
            os.getenv("SENTINELA_CLAYTON_USER", "clayton.bueno").strip(),
            os.getenv("SENTINELA_CLAYTON_PASSWORD", "123456").strip(),
            os.getenv("SENTINELA_CLAYTON_EMAIL", "clayton.bueno@nascel.com.br").strip(),
            "Clayton",
            "Bueno",
        )

        # Carteira Fiscal já cadastrada (CÓD + CNPJ + ops + revisador) a partir da planilha do Fiscal.
        from clientes.carteira_fiscal import aplicar_semente_carteira_fiscal

        resultado = aplicar_semente_carteira_fiscal(criar_pastas_clientes=False)
        self.stdout.write(
            self.style.SUCCESS(
                f"Carteira Fiscal: {resultado['criados']} criados, "
                f"{resultado['atualizados']} atualizados "
                f"(seed {resultado['total_seed']})."
            )
        )

    def _garantir_analista(self, User, username, senha, email, nome, sobrenome):
        if not username or not senha:
            return
        usuario, criado = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "first_name": nome,
                "last_name": sobrenome,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if criado or not usuario.has_usable_password():
            usuario.email = email or usuario.email
            usuario.first_name = usuario.first_name or nome
            usuario.last_name = usuario.last_name or sobrenome
            usuario.is_staff = False
            usuario.is_superuser = False
            usuario.set_password(senha)
            usuario.save()
        if Colaborador.objects.filter(usuario=usuario).exists():
            return
        pessoa = Colaborador.objects.filter(usuario__isnull=True, email__iexact=email).first()
        if pessoa is None:
            Colaborador.objects.create(
                nome=f"{nome} {sobrenome}".strip(),
                cargo="Analista",
                email=email,
                usuario=usuario,
                ativo=True,
                pode_cadastrar_cliente=True,
                pode_editar_cliente=True,
                pode_inativar_cliente=True,
                pode_triar=True,
                pode_auditar=True,
            )
        else:
            pessoa.usuario = usuario
            pessoa.cargo = pessoa.cargo or "Analista"
            pessoa.save(update_fields=["usuario", "cargo"])
