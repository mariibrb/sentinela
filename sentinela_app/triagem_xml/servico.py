import shutil
import tempfile
from pathlib import Path

from clientes.utils_sharepoint import pasta_do_mes
from triagem_xml.garimpeiro_motor import MotorGarimpeiroSentinela


def importar_uploads(empresa, arquivos, competencia: str):
    pasta = pasta_do_mes(empresa, competencia)
    motor = MotorGarimpeiroSentinela(empresa, competencia, str(pasta))
    with tempfile.TemporaryDirectory() as temporario:
        origem = Path(temporario) / "lote"
        origem.mkdir()
        brutos = pasta / "01_XML_BRUTOS_RECEBIDOS"
        brutos.mkdir(parents=True, exist_ok=True)
        for arquivo in arquivos:
            nome = Path(arquivo.name).name
            destino = origem / nome
            with destino.open("wb") as saida:
                for pedaco in arquivo.chunks():
                    saida.write(pedaco)
            shutil.copy(destino, brutos / nome)
        total = motor.processar_lote_arquivos(str(origem))
        from garimpeiro_legado.ponte import processar_garimpo

        ok, texto, _quantidade = processar_garimpo(empresa.cnpj, [origem])
        return total, ok, texto
