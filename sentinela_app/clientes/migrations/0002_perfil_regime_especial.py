import clientes.models
from django.db import migrations, models


def copiar_responsaveis(apps, schema_editor):
    Empresa = apps.get_model("clientes", "Empresa")
    for empresa in Empresa.objects.all():
        nome = empresa.analista_responsavel or ""
        empresa.responsavel_apuracao = nome
        empresa.responsavel_revisao = nome
        empresa.save(update_fields=["responsavel_apuracao", "responsavel_revisao"])


class Migration(migrations.Migration):

    dependencies = [
        ("clientes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="empresa",
            name="responsavel_apuracao",
            field=models.CharField(default="", max_length=100, verbose_name="Responsável pela apuração"),
        ),
        migrations.AddField(
            model_name="empresa",
            name="responsavel_revisao",
            field=models.CharField(default="", max_length=100, verbose_name="Responsável pela revisão"),
        ),
        migrations.RunPython(copiar_responsaveis, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="empresa",
            name="responsavel_apuracao",
            field=models.CharField(max_length=100, verbose_name="Responsável pela apuração"),
        ),
        migrations.AlterField(
            model_name="empresa",
            name="responsavel_revisao",
            field=models.CharField(max_length=100, verbose_name="Responsável pela revisão"),
        ),
        migrations.RemoveField(model_name="empresa", name="analista_responsavel"),
        migrations.RemoveField(model_name="empresa", name="descricao_regime_especial"),
        migrations.AddField(
            model_name="empresa",
            name="documento_regime_especial",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=clientes.models.caminho_documento_regime,
                verbose_name="PDF do PTA / Regime Especial",
            ),
        ),
        migrations.AddField(
            model_name="empresa",
            name="perfil_estabelecimento",
            field=models.CharField(
                choices=[
                    ("GERAL", "Regime Normal / Operação Padrão"),
                    ("ECOMMERCE", "E-commerce / Venda à Distância (DIFAL EC 87/15 e Protocolos ICMS)"),
                    ("INDUSTRIA", "Indústria (insumos, créditos, IPI e ICMS próprio)"),
                    ("CD", "Centro de Distribuição / Armazém Geral (logística e transferência)"),
                    ("REGIME_ESPECIAL_MG", "Regime Especial Concedido por MG (setorial, diferimento, PTA)"),
                    ("REGIME_ESPECIAL_SP", "Regime Especial Concedido por SP (armazém geral, dispensa de emissão, diferimentos)"),
                ],
                default="GERAL",
                max_length=32,
                verbose_name="Perfil / regime especial",
            ),
        ),
        migrations.AlterField(
            model_name="empresa",
            name="regime_pis_cofins",
            field=models.CharField(
                choices=[
                    ("Cumulativo", "Cumulativo"),
                    ("Não-Cumulativo", "Não-Cumulativo"),
                    ("Simples Nacional", "Simples Nacional"),
                ],
                max_length=30,
                verbose_name="Regime de PIS/COFINS",
            ),
        ),
    ]
