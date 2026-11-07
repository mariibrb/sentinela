import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("clientes", "0005_colaboradores"),
    ]

    operations = [
        migrations.AddField(
            model_name="colaborador",
            name="usuario",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="colaborador",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Usuário de acesso",
            ),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_cadastrar_cliente",
            field=models.BooleanField(default=False, verbose_name="Cadastrar cliente"),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_editar_cliente",
            field=models.BooleanField(default=False, verbose_name="Editar cliente"),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_inativar_cliente",
            field=models.BooleanField(default=False, verbose_name="Inativar cliente"),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_excluir_cliente",
            field=models.BooleanField(default=False, verbose_name="Excluir cliente"),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_triar",
            field=models.BooleanField(default=False, verbose_name="Triar XML"),
        ),
        migrations.AddField(
            model_name="colaborador",
            name="pode_auditar",
            field=models.BooleanField(default=False, verbose_name="Auditar e cruzar SPED"),
        ),
    ]
