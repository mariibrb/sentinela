from pathlib import Path

from django.conf import settings
from django.db import models


def caminho_documento_regime(empresa, nome_arquivo):
    cnpj = "".join(caractere for caractere in str(empresa.cnpj or "") if caractere.isdigit()) or "sem-cnpj"
    return f"regimes_especiais/{cnpj}/{Path(nome_arquivo).name}"


class Colaborador(models.Model):
    PERMISSOES = (
        ("pode_cadastrar_cliente", "Cadastrar cliente"),
        ("pode_editar_cliente", "Editar cliente"),
        ("pode_inativar_cliente", "Inativar cliente"),
        ("pode_excluir_cliente", "Excluir cliente"),
        ("pode_triar", "Triar XML"),
        ("pode_auditar", "Auditar e cruzar SPED"),
    )
    nome = models.CharField(max_length=120, verbose_name="Nome")
    cargo = models.CharField(max_length=80, verbose_name="Cargo / função")
    email = models.EmailField(blank=True, verbose_name="E-mail")
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="colaborador",
        verbose_name="Usuário de acesso",
    )
    ativo = models.BooleanField(default=True)
    pode_cadastrar_cliente = models.BooleanField(default=False, verbose_name="Cadastrar cliente")
    pode_editar_cliente = models.BooleanField(default=False, verbose_name="Editar cliente")
    pode_inativar_cliente = models.BooleanField(default=False, verbose_name="Inativar cliente")
    pode_excluir_cliente = models.BooleanField(default=False, verbose_name="Excluir cliente")
    pode_triar = models.BooleanField(default=False, verbose_name="Triar XML")
    pode_auditar = models.BooleanField(default=False, verbose_name="Auditar e cruzar SPED")

    class Meta:
        ordering = ["nome"]
        verbose_name = "Colaborador"
        verbose_name_plural = "Colaboradores"

    def __str__(self):
        return self.nome

    def rotulos_permissoes(self):
        return [rotulo for codigo, rotulo in self.PERMISSOES if getattr(self, codigo)]


class Empresa(models.Model):
    REGIMES_TRIBUTARIOS = [
        ("Simples Nacional", "Simples Nacional"),
        ("Lucro Presumido", "Lucro Presumido"),
        ("Lucro Real", "Lucro Real"),
    ]
    REGIMES_PIS_COFINS = [
        ("Cumulativo", "Cumulativo"),
        ("Não-Cumulativo", "Não-Cumulativo"),
        ("Simples Nacional", "Simples Nacional"),
    ]
    VINCULO_PIS_COFINS = {
        "Lucro Real": "Não-Cumulativo",
        "Lucro Presumido": "Cumulativo",
        "Simples Nacional": "Simples Nacional",
    }
    ANEXOS_SIMPLES = [
        ("I", "Anexo I"),
        ("II", "Anexo II"),
        ("III", "Anexo III"),
        ("IV", "Anexo IV"),
        ("V", "Anexo V"),
    ]
    PERFIS_ESTABELECIMENTO = [
        ("GERAL", "Regime Normal / Operação Padrão"),
        ("ECOMMERCE", "E-commerce / Venda à Distância (DIFAL EC 87/15 e Protocolos ICMS)"),
        ("INDUSTRIA", "Indústria (insumos, créditos, IPI e ICMS próprio)"),
        ("CD", "Centro de Distribuição / Armazém Geral (logística e transferência)"),
        ("REGIME_ESPECIAL_MG", "Regime Especial Concedido por MG (setorial, diferimento, PTA)"),
        ("REGIME_ESPECIAL_SP", "Regime Especial Concedido por SP (armazém geral, dispensa de emissão, diferimentos)"),
    ]
    PERFIS_COM_PDF = {"REGIME_ESPECIAL_MG", "REGIME_ESPECIAL_SP"}
    # Bloco A: Identificação e Controle Domínio
    codigo_dominio = models.CharField(max_length=4, unique=True, verbose_name="Código Domínio")
    cnpj = models.CharField(max_length=14, unique=True, verbose_name="CNPJ")
    tipo_estabelecimento = models.CharField(
        max_length=10,
        choices=[("Matriz", "Matriz"), ("Filial", "Filial")],
        default="Matriz",
    )
    razao_social = models.CharField(max_length=255, verbose_name="Razão Social")
    nome_fantasia = models.CharField(max_length=255, blank=True, null=True, verbose_name="Nome Fantasia / Pasta")
    status = models.CharField(max_length=20, default="Ativo", verbose_name="Status")
    responsavel_apuracao = models.ForeignKey(
        Colaborador,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="apuracoes",
        verbose_name="Responsável pela apuração",
    )
    responsavel_revisao = models.ForeignKey(
        Colaborador,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="revisoes",
        verbose_name="Responsável pela revisão",
    )

    # Bloco B: Inscrições e Dados Cadastrais
    inscricao_estadual = models.CharField(max_length=30, blank=True, default="", verbose_name="IE Principal")
    inscricao_municipal = models.CharField(max_length=30, blank=True, null=True, verbose_name="Inscrição Municipal")
    suframa = models.CharField(max_length=20, blank=True, null=True)
    cnae_principal = models.CharField(max_length=10, verbose_name="CNAE Principal")

    # Bloco C: Localização e SPED
    cep = models.CharField(max_length=10)
    logradouro = models.CharField(max_length=255)
    numero = models.CharField(max_length=20)
    complemento = models.CharField(max_length=100, blank=True, null=True)
    bairro = models.CharField(max_length=100)
    uf = models.CharField(max_length=2)
    municipio = models.CharField(max_length=100)
    codigo_ibge = models.CharField(max_length=7, verbose_name="Código IBGE (SPED)")

    # Bloco D: Parâmetros Tributários
    regime_tributario = models.CharField(
        max_length=50,
        choices=REGIMES_TRIBUTARIOS,
        verbose_name="Regime Tributário",
    )
    anexo_simples = models.CharField(
        max_length=20,
        blank=True,
        null=True,
        choices=ANEXOS_SIMPLES,
        verbose_name="Anexo do Simples",
    )
    regime_pis_cofins = models.CharField(
        max_length=30,
        choices=REGIMES_PIS_COFINS,
        verbose_name="Regime de PIS/COFINS",
    )
    perfil_estabelecimento = models.CharField(
        max_length=32,
        choices=PERFIS_ESTABELECIMENTO,
        default="GERAL",
        verbose_name="Perfil / regime especial",
    )
    contribuinte_icms = models.BooleanField(default=True)
    contribuinte_ipi = models.BooleanField(default=False)
    possui_regime_especial = models.BooleanField(default=False, verbose_name="Regime Especial Mineiro / Outros")
    documento_regime_especial = models.FileField(
        upload_to=caminho_documento_regime,
        blank=True,
        null=True,
        verbose_name="PDF do PTA / Regime Especial",
    )

    data_cadastro = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["codigo_dominio"]
        verbose_name = "Empresa"
        verbose_name_plural = "Empresas"

    def __str__(self):
        return f"{self.codigo_dominio} - {self.razao_social} ({self.cnpj})"

    @classmethod
    def pis_cofins_do_regime(cls, regime_tributario):
        return cls.VINCULO_PIS_COFINS.get(regime_tributario, "")

    def save(self, *args, **kwargs):
        vinculado = self.pis_cofins_do_regime(self.regime_tributario)
        if vinculado:
            self.regime_pis_cofins = vinculado
            campos = kwargs.get("update_fields")
            if campos is not None:
                kwargs["update_fields"] = set(campos) | {"regime_pis_cofins"}
        super().save(*args, **kwargs)


class DocumentoXML(models.Model):
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="documentos")
    competencia = models.CharField(max_length=7, verbose_name="Competência (MMAAAA)")
    modelo = models.CharField(max_length=2)
    chave_acesso = models.CharField(max_length=44, unique=True)
    numero_nota = models.CharField(max_length=20)
    serie = models.CharField(max_length=10)
    data_emissao = models.DateField()
    tipo_operacao = models.CharField(max_length=30)
    status_sefa = models.CharField(max_length=30)

    valor_total = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    base_icms = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_icms = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    base_icms_st = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_icms_st = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_ipi = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_pis = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_cofins = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    possui_divergencia = models.BooleanField(default=False)
    mensagem_divergencia = models.TextField(blank=True, null=True)
    sugestao_correcao = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        ordering = ["serie", "numero_nota"]
        verbose_name = "Documento XML"
        verbose_name_plural = "Documentos XML"
        indexes = [models.Index(fields=["empresa", "competencia"])]

    def __str__(self):
        return f"{self.modelo} {self.numero_nota} · {self.chave_acesso}"


class LogAlteracao(models.Model):
    ACOES = [
        ("CADASTRO", "Cadastro"),
        ("REGIME", "Regime"),
        ("TRIAGEM", "Triagem de XML"),
        ("AJUSTE", "Ajuste fiscal"),
        ("BACKUP", "Backup do acervo"),
    ]
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logs_sentinela",
    )
    quando = models.DateTimeField(auto_now_add=True)
    empresa = models.ForeignKey(
        Empresa,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="logs",
    )
    acao = models.CharField(max_length=20, choices=ACOES)
    campo = models.CharField(max_length=80)
    valor_anterior = models.TextField(blank=True)
    valor_novo = models.TextField(blank=True)

    class Meta:
        ordering = ["-quando", "-id"]
        verbose_name = "Log de alteração"
        verbose_name_plural = "Logs de alteração"
        indexes = [models.Index(fields=["empresa", "quando"])]

    def __str__(self):
        return f"{self.quando:%d/%m/%Y %H:%M} {self.campo}"
