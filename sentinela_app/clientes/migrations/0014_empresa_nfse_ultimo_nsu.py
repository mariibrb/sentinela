from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("clientes", "0013_ramo_cnae_saida_e_nomes_escopo"),
    ]

    operations = [
        migrations.AddField(
            model_name="empresa",
            name="nfse_ultimo_nsu",
            field=models.PositiveIntegerField(
                default=0,
                help_text="Cursor da distribuição ADN para sincronizar XMLs novos deste cliente.",
                verbose_name="Último NSU ADN (NFS-e)",
            ),
        ),
    ]
