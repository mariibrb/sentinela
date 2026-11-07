import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clientes", "0006_permissoes_colaborador"),
    ]

    operations = [
        migrations.AlterField(
            model_name="empresa",
            name="inscricao_estadual",
            field=models.CharField(blank=True, default="", max_length=30, verbose_name="IE Principal"),
        ),
        migrations.AlterField(
            model_name="empresa",
            name="responsavel_apuracao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="apuracoes",
                to="clientes.colaborador",
                verbose_name="Responsável pela apuração",
            ),
        ),
        migrations.AlterField(
            model_name="empresa",
            name="responsavel_revisao",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="revisoes",
                to="clientes.colaborador",
                verbose_name="Responsável pela revisão",
            ),
        ),
    ]
