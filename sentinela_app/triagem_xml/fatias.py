"""Lê um lote grande em partes curtas, para o processo não cair no meio."""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from django.conf import settings

from triagem_xml.garimpeiro_motor import MotorGarimpeiroSentinela, _PROFUNDIDADE_MAXIMA_ZIP

POR_VEZ = 30
_NOME_ESTADO = "estado.json"


def token_valido(token: str) -> bool:
    return len(token) == 16 and all(caractere in "0123456789abcdef" for caractere in token)


def pasta_do_lote(usuario_id, token: str) -> Path:
    seguro = "".join(caractere for caractere in token if caractere.isalnum())[:32]
    return Path(settings.MEDIA_ROOT) / "lotes_triagem" / str(usuario_id) / seguro


def _carregar(pasta: Path) -> dict:
    caminho = pasta / _NOME_ESTADO
    if not caminho.exists():
        return {"pilha": [], "soltos": [], "seq": 0}
    return json.loads(caminho.read_text(encoding="utf-8"))


def _salvar(pasta: Path, estado: dict) -> None:
    (pasta / _NOME_ESTADO).write_text(json.dumps(estado), encoding="utf-8")


def _membro_seguro(info, destino: Path):
    nome = Path(info.filename)
    if nome.is_absolute() or ".." in nome.parts:
        return None
    alvo = (destino / nome).resolve()
    try:
        alvo.relative_to(destino.resolve())
    except ValueError:
        return None
    return alvo


def _extrair_membro(pacote, info, alvo: Path) -> bool:
    try:
        alvo.parent.mkdir(parents=True, exist_ok=True)
        with pacote.open(info) as origem, alvo.open("wb") as saida:
            shutil.copyfileobj(origem, saida, length=256 * 1024)
        return True
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return False


def _processar_um_xml(motor, caminho: Path) -> int:
    try:
        if not caminho.is_file():
            return 0
        return 1 if motor._processar_xml_individual(str(caminho)) else 0
    except (OSError, zipfile.BadZipFile):
        return 0
    finally:
        try:
            caminho.unlink(missing_ok=True)
        except OSError:
            pass


def _consumir_soltos(motor, estado: dict, faltam: int) -> tuple[int, int]:
    gravados = 0
    usados = 0
    soltos = estado["soltos"]
    while soltos and usados < faltam:
        caminho = Path(soltos.pop(0))
        gravados += _processar_um_xml(motor, caminho)
        usados += 1
    return gravados, usados


def _consumir_zip(motor, pasta: Path, estado: dict, faltam: int) -> int:
    gravados = 0
    pilha = estado["pilha"]
    while pilha and faltam > 0:
        topo = pilha[-1]
        if int(topo["profundidade"]) > _PROFUNDIDADE_MAXIMA_ZIP:
            pilha.pop()
            continue
        caminho_zip = Path(topo["zip"])
        try:
            pacote = zipfile.ZipFile(caminho_zip)
        except (zipfile.BadZipFile, OSError):
            pilha.pop()
            continue
        esgotou = False
        desceu = False
        try:
            infos = pacote.infolist()
            while int(topo["indice"]) < len(infos) and faltam > 0:
                info = infos[int(topo["indice"])]
                topo["indice"] = int(topo["indice"]) + 1
                if info.is_dir():
                    continue
                if _membro_seguro(info, pasta / "saida") is None:
                    continue
                sufixo = Path(info.filename).suffix.lower()
                if sufixo == ".xml":
                    estado["seq"] = int(estado.get("seq", 0)) + 1
                    destino = pasta / "atual" / f"{estado['seq']}.xml"
                    if _extrair_membro(pacote, info, destino):
                        gravados += _processar_um_xml(motor, destino)
                    faltam -= 1
                elif sufixo == ".zip":
                    estado["seq"] = int(estado.get("seq", 0)) + 1
                    aninhado = pasta / "aninhados" / f"z{estado['seq']}.zip"
                    if _extrair_membro(pacote, info, aninhado):
                        pilha.append({
                            "zip": str(aninhado),
                            "indice": 0,
                            "profundidade": int(topo["profundidade"]) + 1,
                        })
                        desceu = True
                        break
            esgotou = int(topo["indice"]) >= len(infos) and not desceu
        finally:
            pacote.close()
        if esgotou:
            pilha.pop()
            if "aninhados" in caminho_zip.parts:
                caminho_zip.unlink(missing_ok=True)
    return gravados


def _guardar(pasta: Path, estado: dict, arquivos, brutos: Path) -> None:
    brutos.mkdir(parents=True, exist_ok=True)
    for arquivo in arquivos:
        nome = Path(getattr(arquivo, "name", "arquivo")).name
        estado["seq"] = int(estado.get("seq", 0)) + 1
        destino = pasta / "entrada" / f"{estado['seq']:05d}_{nome}"
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("wb") as saida:
            for pedaco in arquivo.chunks():
                saida.write(pedaco)
        shutil.copy(destino, brutos / destino.name)
        if nome.lower().endswith(".zip"):
            estado["pilha"].append({"zip": str(destino), "indice": 0, "profundidade": 0})
        elif nome.lower().endswith(".xml"):
            estado["soltos"].append(str(destino))


def avancar(empresa, competencia: str, usuario_id, token: str, arquivos, por_vez: int = POR_VEZ) -> dict:
    from clientes.utils_sharepoint import pasta_do_mes

    pasta = pasta_do_lote(usuario_id, token)
    pasta.mkdir(parents=True, exist_ok=True)
    estado = _carregar(pasta)
    if arquivos:
        _guardar(pasta, estado, arquivos, pasta_do_mes(empresa, competencia) / "01_XML_BRUTOS_RECEBIDOS")
    motor = MotorGarimpeiroSentinela(empresa, competencia, str(pasta_do_mes(empresa, competencia)))
    gravados, usados = _consumir_soltos(motor, estado, por_vez)
    gravados += _consumir_zip(motor, pasta, estado, por_vez - usados)
    continua = bool(estado["pilha"] or estado["soltos"])
    if continua:
        _salvar(pasta, estado)
    else:
        shutil.rmtree(pasta, ignore_errors=True)
    return {"gravados": gravados, "continua": continua, "token": token}
