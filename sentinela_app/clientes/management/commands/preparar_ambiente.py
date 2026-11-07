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

        colab_user = os.getenv("SENTINELA_COLABORADOR_USER", "mariana.barbosa").strip()
        colab_pass = os.getenv("SENTINELA_COLABORADOR_PASSWORD", "").strip()
        colab_email = os.getenv("SENTINELA_COLABORADOR_EMAIL", "mariana.barbosa@nascel.com.br").strip()

        if colab_user and colab_pass:
            colaborador, criado = User.objects.get_or_create(
                username=colab_user,
                defaults={
                    "email": colab_email,
                    "first_name": "Mariana",
                    "last_name": "Barbosa",
                    "is_staff": False,
                    "is_superuser": False,
                },
            )
            if criado or not colaborador.has_usable_password():
                colaborador.set_password(colab_pass)
                colaborador.save()
            if not Colaborador.objects.filter(usuario=colaborador).exists():
                pessoa = Colaborador.objects.filter(usuario__isnull=True, email__iexact=colab_email).first()
                if pessoa is None:
                    Colaborador.objects.create(
                        nome=f"{colaborador.first_name} {colaborador.last_name}".strip() or colaborador.username,
                        cargo="Analista",
                        email=colab_email,
                        usuario=colaborador,
                        ativo=True,
                        pode_cadastrar_cliente=True,
                        pode_editar_cliente=True,
                        pode_inativar_cliente=True,
                        pode_triar=True,
                        pode_auditar=True,
                    )
                else:
                    pessoa.usuario = colaborador
                    pessoa.save(update_fields=["usuario"])
