import shutil
from pathlib import Path

from django.conf import settings

from auditoria.apuracao_motor import MotorApuracaoSentinela
from clientes.governance import CadastroIncompleto, validar_cadastro_para_apuracao
from clientes.models import DocumentoXML
from clientes.utils_sharepoint import pasta_do_mes, pasta_relatorios


def _relatorio(empresa, competencia):
    linhas = [
        "Sentinela",
        f"Empresa: {empresa.codigo_dominio} · {empresa.razao_social}",
        f"CNPJ: {empresa.cnpj}",
        f"Competência: {competencia}",
        f"Regime tributário: {empresa.regime_tributario}",
        f"PIS/COFINS: {empresa.regime_pis_cofins}",
        f"Anexo do Simples: {empresa.anexo_simples or ''}",
        f"Perfil: {empresa.get_perfil_estabelecimento_display()}",
    ]
    documentos = DocumentoXML.objects.filter(empresa=empresa, competencia=competencia)
    linhas.append(f"XML da competência: {documentos.count()}")
    for nota in documentos.filter(possui_divergencia=True):
        linhas.append(
            f"Divergência {nota.numero_nota} {nota.chave_acesso}: "
            f"{nota.mensagem_divergencia} | {nota.sugestao_correcao}"
        )
    try:
        validar_cadastro_para_apuracao(empresa)
        motor = MotorApuracaoSentinela(empresa, competencia)
        icms = motor.calcular_apuracao_icms()
        pis = motor.calcular_pis_cofins()
        linhas.extend([
            f"Débitos de ICMS: {icms['total_debitos']}",
            f"Créditos de ICMS: {icms['total_creditos']}",
            f"Saldo de ICMS: {icms['saldo_icms']}",
            f"ICMS-ST: {icms['total_icms_st']}",
            f"PIS: {pis['pis']}",
            f"COFINS: {pis['cofins']}",
            pis["explicacao"],
        ])
    except CadastroIncompleto as erro:
        linhas.append(str(erro))
    return "\n".join(linhas) + "\n"


def exportar_pacote(empresa, competencia):
    """Monta o mês no acervo e espelha o pacote na pasta mestre do SharePoint."""
    origem = pasta_do_mes(empresa, competencia)
    relatorio = pasta_relatorios(empresa, competencia) / f"relatorio_{competencia}.txt"
    relatorio.write_text(_relatorio(empresa, competencia), encoding="utf-8")
    destino_raiz = Path(settings.SHAREPOINT_ROOT_DIR)
    pasta_mae = Path(settings.PASTA_MAE)
    destino = destino_raiz / origem.relative_to(pasta_mae)
    if origem.resolve() != destino.resolve():
        shutil.copytree(origem, destino, dirs_exist_ok=True)
    return destino
