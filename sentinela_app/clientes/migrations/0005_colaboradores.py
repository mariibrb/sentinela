import django.db.models.deletion
from django.db import migrations, models


def vincular_responsaveis(apps, schema_editor):
    Empresa = apps.get_model("clientes", "Empresa")
    Colaborador = apps.get_model("clientes", "Colaborador")
    cache = {}

    def pessoa(nome):
        nome = (nome or "").strip()
        if not nome:
            return None
        if nome not in cache:
            cache[nome], _criado = Colaborador.objects.get_or_create(
                nome=nome,
                defaults={"cargo": "Analista", "email": "", "ativo": True},
            )
        return cache[nome]

    for empresa in Empresa.objects.all():
        empresa.responsavel_apuracao_novo = pessoa(empresa.responsavel_apuracao)
        empresa.responsavel_revisao_novo = pessoa(empresa.responsavel_revisao)
        empresa.save(update_fields=["responsavel_apuracao_novo", "responsavel_revisao_novo"])


class Migration(migrations.Migration):

    dependencies = [
        ("clientes", "0004_log_alteracao"),
    ]

    operations = [
        migrations.CreateModel(
            name="Colaborador",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nome", models.CharField(max_length=120, verbose_name="Nome")),
                ("cargo", models.CharField(max_length=80, verbose_name="Cargo / função")),
                ("email", models.EmailField(blank=True, max_length=254, verbose_name="E-mail")),
                ("ativo", models.BooleanField(default=True)),
            ],
            options={
                "verbose_name": "Colaborador",
                "verbose_name_plural": "Colaboradores",
                "ordering": ["nome"],
            },
        ),
        migrations.AddField(
            model_name="empresa",
            name="responsavel_apuracao_novo",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="apuracoes",
                to="clientes.colaborador",
                verbose_name="Responsável pela apuração",
            ),
        ),
        migrations.AddField(
            model_name="empresa",
            name="responsavel_revisao_novo",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="revisoes",
                to="clientes.colaborador",
                verbose_name="Responsável pela revisão",
            ),
        ),
        migrations.RunPython(vincular_responsaveis, migrations.RunPython.noop),
        migrations.RemoveField(model_name="empresa", name="responsavel_apuracao"),
        migrations.RemoveField(model_name="empresa", name="responsavel_revisao"),
        migrations.RenameField(model_name="empresa", old_name="responsavel_apuracao_novo", new_name="responsavel_apuracao"),
        migrations.RenameField(model_name="empresa", old_name="responsavel_revisao_novo", new_name="responsavel_revisao"),
    ]
