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
        ("MEI", "MEI"),
        ("Simples Nacional", "Simples Nacional"),
        ("Lucro Presumido", "Lucro Presumido"),
        ("Lucro Real", "Lucro Real"),
        ("Imune/Isenta", "Imune/Isenta"),
    ]
    REGIMES_PIS_COFINS = [
        ("Cumulativo", "Cumulativo"),
        ("Não-Cumulativo", "Não-Cumulativo"),
        ("Simples Nacional", "Simples Nacional"),
        ("Isento / Imune", "Isento / Imune"),
    ]
    VINCULO_PIS_COFINS = {
        "Lucro Real": "Não-Cumulativo",
        "Lucro Presumido": "Cumulativo",
        "Simples Nacional": "Simples Nacional",
        "MEI": "Simples Nacional",
        "Imune/Isenta": "Isento / Imune",
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

    # Bloco E: Onboarding, Contrato, Contatos e Escopo de Trabalho
    data_abertura = models.CharField(max_length=20, blank=True, default="", verbose_name="Data de Abertura")
    data_inicio_cliente = models.CharField(max_length=10, blank=True, default="", verbose_name="Cliente a partir de (MM/AAAA)")
    contatos = models.JSONField(default=list, blank=True, verbose_name="Contatos dos Responsáveis")
    resumo_cliente = models.TextField(blank=True, default="", verbose_name="Resumo / Histórico do Cliente")

    escopo_fiscal = models.JSONField(default=dict, blank=True, verbose_name="Escopo: Compliance Fiscal")
    qtd_notas_entrada = models.CharField(max_length=40, blank=True, default="", verbose_name="Qtd média Entradas")
    qtd_notas_saida = models.CharField(max_length=40, blank=True, default="", verbose_name="Qtd média Saídas")
    qtd_emissao_notas_servico = models.CharField(max_length=40, blank=True, default="", verbose_name="Qtd Emissão Serviços")
    qtd_emissao_notas_produto = models.CharField(max_length=40, blank=True, default="", verbose_name="Qtd Emissão Produtos")
    tipos_icms_incentivado = models.JSONField(default=list, blank=True, verbose_name="Incentivos TTS (ICMS Incentivado)")
    estados_icms_st = models.JSONField(default=list, blank=True, verbose_name="Estados Apuração ICMS-ST")

    escopo_contabil = models.JSONField(default=dict, blank=True, verbose_name="Escopo: Contabilidade")
    escopo_dp = models.JSONField(default=dict, blank=True, verbose_name="Escopo: Departamento Pessoal")
    escopo_regularizacao = models.JSONField(default=dict, blank=True, verbose_name="Escopo: Regularização / Abertura")
    estados_st = models.JSONField(default=list, blank=True, verbose_name="Estados para Inscrição ST")
    busca_escopo = models.TextField(blank=True, default="", verbose_name="Índice de pesquisa do escopo")

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
        from clientes.escopo import montar_busca_escopo

        vinculado = self.pis_cofins_do_regime(self.regime_tributario)
        if vinculado:
            self.regime_pis_cofins = vinculado
            campos = kwargs.get("update_fields")
            if campos is not None:
                kwargs["update_fields"] = set(campos) | {"regime_pis_cofins"}
        self.busca_escopo = montar_busca_escopo(self)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"busca_escopo"}
        super().save(*args, **kwargs)

    def resumo_escopo(self):
        from clientes.escopo import resumo_escopo_empresa

        return resumo_escopo_empresa(self)

    def texto_substituicoes(self):
        if not self.pk:
            return ""
        return ", ".join(f"{item.uf} {item.numero}" for item in self.substituicoes.all())


class InscricaoSubstituto(models.Model):
    UFS = (
        ("AC", "Acre"), ("AL", "Alagoas"), ("AM", "Amazonas"), ("AP", "Amapá"),
        ("BA", "Bahia"), ("CE", "Ceará"), ("DF", "Distrito Federal"), ("ES", "Espírito Santo"),
        ("GO", "Goiás"), ("MA", "Maranhão"), ("MG", "Minas Gerais"), ("MS", "Mato Grosso do Sul"),
        ("MT", "Mato Grosso"), ("PA", "Pará"), ("PB", "Paraíba"), ("PE", "Pernambuco"),
        ("PI", "Piauí"), ("PR", "Paraná"), ("RJ", "Rio de Janeiro"), ("RN", "Rio Grande do Norte"),
        ("RO", "Rondônia"), ("RR", "Roraima"), ("RS", "Rio Grande do Sul"), ("SC", "Santa Catarina"),
        ("SE", "Sergipe"), ("SP", "São Paulo"), ("TO", "Tocantins"),
    )
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="substituicoes")
    uf = models.CharField(max_length=2, choices=UFS)
    numero = models.CharField(max_length=30, verbose_name="Inscrição")

    class Meta:
        ordering = ["uf"]
        constraints = [models.UniqueConstraint(fields=["empresa", "uf"], name="inscricao_substituto_unica")]
        verbose_name = "Inscrição de substituto tributário"
        verbose_name_plural = "Inscrições de substituto tributário"

    def __str__(self):
        return f"{self.uf} {self.numero}"


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


class NotaServico(models.Model):
    TIPOS = [
        ("PRESTADO", "Serviço Prestado (Emitida)"),
        ("TOMADO", "Serviço Tomado (Recebida)"),
    ]
    ETAPAS_ESCRITURACAO = [
        ("PENDENTE", "Falta escriturar / conferir"),
        ("ESCRITURADA", "Escriturada no mês"),
        ("MES_SEGUINTE", "Ficará para o mês seguinte"),
        ("NAO_ESCRITURAR", "Cancelada / Não escriturar"),
    ]
    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="notas_servico")
    competencia = models.CharField(max_length=7, verbose_name="Competência da coleta (MMAAAA)")
    tipo = models.CharField(max_length=15, choices=TIPOS, default="TOMADO")
    numero = models.CharField(max_length=30, blank=True, default="")
    chave_acesso = models.CharField(max_length=60)
    data_emissao = models.DateField(null=True, blank=True)
    data_emissao_texto = models.CharField(max_length=30, blank=True, default="")
    competencia_emissao = models.CharField(max_length=10, blank=True, default="")
    razao_terceiro = models.CharField(max_length=255, blank=True, default="", verbose_name="Tomador / Prestador")
    codigo_tributacao_nacional = models.CharField(max_length=255, blank=True, default="")

    valor_servico = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    valor_liquido = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    retencao = models.CharField(max_length=80, blank=True, default="")
    valor_issqn = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    contrib_sociais_retidas = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    contrib_previdenciaria_retida = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)
    irrf = models.DecimalField(max_digits=15, decimal_places=2, default=0.00)

    status = models.CharField(max_length=30, default="Ativa")
    data_cancelamento = models.CharField(max_length=40, blank=True, default="")

    tem_xml = models.BooleanField(default=False)
    tem_pdf = models.BooleanField(default=False)
    caminho_xml = models.CharField(max_length=500, blank=True, default="")
    caminho_pdf = models.CharField(max_length=500, blank=True, default="")

    etapa_escrituracao = models.CharField(
        max_length=25,
        choices=ETAPAS_ESCRITURACAO,
        default="PENDENTE",
    )
    competencia_escrituracao = models.CharField(max_length=7, blank=True, default="")
    possui_alerta = models.BooleanField(default=False)
    motivo_alerta = models.TextField(blank=True, default="")
    sugestao_acao = models.CharField(max_length=255, blank=True, default="")
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["tipo", "numero", "id"]
        verbose_name = "Nota Fiscal de Serviço (NFS-e)"
        verbose_name_plural = "Notas Fiscais de Serviço (NFS-e)"
        constraints = [
            models.UniqueConstraint(
                fields=["empresa", "tipo", "chave_acesso"],
                name="nfse_empresa_tipo_chave_unica",
            )
        ]
        indexes = [
            models.Index(fields=["empresa", "competencia"]),
            models.Index(fields=["empresa", "competencia_escrituracao"]),
        ]

    def __str__(self):
        return f"NFS-e {self.get_tipo_display()} Nº {self.numero} · {self.razao_terceiro}"


class LogAlteracao(models.Model):
    ACOES = [
        ("CADASTRO", "Cadastro"),
        ("REGIME", "Regime"),
        ("ESCOPO", "Escopo de trabalho"),
        ("TRIAGEM", "Triagem de XML"),
        ("COLETOR", "Apanhador NFS-e"),
        ("AJUSTE", "Ajuste fiscal"),
        ("BACKUP", "Backup do acervo"),
        ("ENTRADA", "Entrada"),
        ("SAIDA", "Saída"),
        ("EXCLUSAO", "Exclusão"),
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

    def tipo_feito(self):
        if self.acao == "ENTRADA":
            return "Entrada"
        if self.acao == "SAIDA":
            return "Saída"
        if self.acao == "EXCLUSAO":
            return "Exclusão"
        if not (self.valor_anterior or "").strip():
            return "Inclusão"
        return "Alteração"
