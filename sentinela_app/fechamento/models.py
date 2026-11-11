from django.conf import settings
from django.db import models

from clientes.models import Empresa


class ChecklistFechamento(models.Model):
    REGIMES = [
        ("simples", "Simples Nacional"),
        ("presumido", "Lucro Presumido"),
        ("real", "Lucro Real"),
        ("mei", "MEI"),
    ]
    TIPOS = [
        ("importacao", "Importação"),
        ("analise", "Análise"),
        ("obrigacoes", "Obrigações Acessórias"),
        ("fechamento", "Fechamento"),
    ]
    STATUS = [
        ("em_execucao", "Em execução"),
        ("aguardando_revisao", "Aguardando revisão"),
        ("aprovado", "Aprovado"),
        ("reprovado", "Reprovado"),
    ]

    empresa = models.ForeignKey(Empresa, on_delete=models.CASCADE, related_name="checklists_fechamento")
    competencia = models.CharField(max_length=7, verbose_name="Competência (MMAAAA)")
    regime = models.CharField(max_length=20, choices=REGIMES, default="simples")
    checklist_tipo = models.CharField(max_length=20, choices=TIPOS, default="importacao")
    status = models.CharField(max_length=30, choices=STATUS, default="em_execucao")
    flags = models.JSONField(default=dict, blank=True)
    obs_logs = models.JSONField(default=dict, blank=True)
    anexos = models.JSONField(default=dict, blank=True)
    operador = models.CharField(max_length=120, blank=True, default="")
    revisor = models.CharField(max_length=120, blank=True, default="")
    revisor_usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="fechamentos_revisados",
        verbose_name="Usuário revisor (Sentinela)",
    )
    ie_st = models.CharField(max_length=40, blank=True, default="nao_aplica")
    aprovado_em = models.DateTimeField(null=True, blank=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Checklist de Fechamento"
        verbose_name_plural = "Checklists de Fechamento"
        constraints = [
            models.UniqueConstraint(fields=["empresa", "competencia"], name="fechamento_empresa_competencia_unica")
        ]
        indexes = [models.Index(fields=["empresa", "competencia"])]

    def __str__(self):
        return f"{self.empresa.codigo_dominio} · {self.competencia} · {self.get_regime_display()}"
