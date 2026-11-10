"""Cliente ADN (Ambiente de Dados Nacional) para baixar XMLs de NFS-e no Apanhador.

Usa o certificado digital já instalado no Windows (dono do escritório) via
requests-schannel e consulta cada cliente com cnpjConsulta (procuração RF).
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from clientes.competencia import montar
from clientes.models import Empresa
from triagem_xml.coletor_nfse import importar_xml_nfse

ADN_BASE = "https://adn.nfse.gov.br/contribuintes"
CONFIG_ARQUIVO = "adn_certificado.json"
MAX_LOTES_POR_SYNC = 200


@dataclass
class CertificadoWindows:
    thumbprint: str
    subject: str
    friendly_name: str = ""


@dataclass
class DocumentoAdn:
    nsu: int
    chave_acesso: str
    tipo_documento: str
    xml_bytes: bytes


class AdnIndisponivel(RuntimeError):
    pass


def _pasta_config() -> Path:
    from django.conf import settings

    base = Path(getattr(settings, "PASTA_MAE", "") or tempfile.gettempdir()) / "Sentinela"
    base.mkdir(parents=True, exist_ok=True)
    return base


def caminho_config_certificado() -> Path:
    return _pasta_config() / CONFIG_ARQUIVO


def carregar_config_certificado() -> dict:
    caminho = caminho_config_certificado()
    if not caminho.is_file():
        return {}
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:
        return {}


def salvar_config_certificado(*, thumbprint: str, subject: str = "") -> None:
    caminho = caminho_config_certificado()
    caminho.write_text(
        json.dumps(
            {"thumbprint": (thumbprint or "").strip().upper(), "subject": subject or ""},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def ambiente_windows_local() -> bool:
    return os.name == "nt"


def listar_certificados_windows() -> list[CertificadoWindows]:
    """Lista certificados pessoais (MY / CurrentUser) via PowerShell."""
    if not ambiente_windows_local():
        return []
    import subprocess

    script = r"""
$ErrorActionPreference = 'Stop'
$store = New-Object System.Security.Cryptography.X509Certificates.X509Store('My','CurrentUser')
$store.Open('ReadOnly')
$rows = foreach ($c in $store.Certificates) {
  [PSCustomObject]@{
    Thumbprint = $c.Thumbprint
    Subject = $c.Subject
    FriendlyName = $c.FriendlyName
  }
}
$store.Close()
$rows | ConvertTo-Json -Compress
"""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=flags,
        )
    except Exception as exc:
        raise AdnIndisponivel(f"Não consegui ler o repositório de certificados do Windows: {exc}") from exc
    if proc.returncode != 0:
        raise AdnIndisponivel((proc.stderr or proc.stdout or "Falha ao listar certificados.").strip())
    bruto = (proc.stdout or "").strip()
    if not bruto:
        return []
    dados = json.loads(bruto)
    if isinstance(dados, dict):
        dados = [dados]
    saida = []
    for item in dados:
        thumb = str(item.get("Thumbprint") or "").strip().upper()
        if not thumb:
            continue
        saida.append(
            CertificadoWindows(
                thumbprint=thumb,
                subject=str(item.get("Subject") or ""),
                friendly_name=str(item.get("FriendlyName") or ""),
            )
        )
    return saida


def _criar_sessao_mtls(thumbprint: str):
    if not ambiente_windows_local():
        raise AdnIndisponivel("A API ADN com certificado só funciona no Windows local do escritório.")
    try:
        from requests_schannel import create_session
    except ImportError as exc:
        raise AdnIndisponivel(
            "Falta o pacote requests-schannel neste Python. Instale e reinicie o Sentinela local."
        ) from exc
    thumb = (thumbprint or "").strip().upper()
    if not thumb:
        raise AdnIndisponivel("Escolha o certificado do dono do escritório no Apanhador.")
    return create_session(client_cert_thumbprint=thumb)


def decodificar_xml_dfe(xml_b64: str) -> bytes:
    bruto = base64.b64decode(xml_b64 or "")
    if bruto.startswith(b"\x1f\x8b"):
        return gzip.decompress(bruto)
    return bruto


def competencia_do_xml(xml_bytes: bytes) -> str | None:
    """Extrai competência MMAAAA a partir de dCompet / dhEmi no XML."""
    texto = xml_bytes.decode("utf-8", errors="ignore")
    m = re.search(r"<dCompet>(\d{4})-(\d{2})", texto)
    if m:
        return montar(int(m.group(2)), int(m.group(1)))
    m = re.search(r"<dhEmi>(\d{4})-(\d{2})", texto)
    if m:
        return montar(int(m.group(2)), int(m.group(1)))
    return None


def consultar_lote_dfe(session, nsu: int, cnpj_consulta: str) -> dict:
    cnpj = re.sub(r"\D", "", cnpj_consulta or "")
    url = f"{ADN_BASE}/DFe/{int(nsu)}"
    params = {}
    if len(cnpj) == 14:
        params["cnpjConsulta"] = cnpj
    resp = session.get(url, params=params, headers={"Accept": "application/json"}, timeout=60)
    if resp.status_code == 404:
        return {"LoteDFe": [], "StatusProcessamento": "NENHUM"}
    if not resp.ok:
        detalhe = ""
        try:
            detalhe = resp.json()
        except Exception:
            detalhe = (resp.text or "")[:500]
        texto = str(detalhe)
        if "E2245" in texto or "procura" in texto.lower():
            raise AdnIndisponivel(
                "Procuração ausente ou inválida na Receita Federal para este CNPJ "
                f"({cnpj}). Cadastre a procuração em servicos.receitafederal.gov.br e tente de novo."
            )
        raise AdnIndisponivel(f"ADN HTTP {resp.status_code}: {texto}")
    try:
        return resp.json()
    except Exception as exc:
        raise AdnIndisponivel("Resposta ADN inválida (não é JSON).") from exc


def parse_lote(dados: dict) -> list[DocumentoAdn]:
    docs: list[DocumentoAdn] = []
    for item in dados.get("LoteDFe") or []:
        try:
            nsu = int(item.get("NSU") or 0)
        except (TypeError, ValueError):
            continue
        tipo = str(item.get("TipoDocumento") or item.get("TipoEvento") or "").upper()
        chave = re.sub(r"\D", "", str(item.get("ChaveAcesso") or ""))
        xml_b64 = item.get("ArquivoXml") or item.get("XmlDFe") or item.get("arquivoXml") or ""
        if not xml_b64:
            continue
        try:
            xml_bytes = decodificar_xml_dfe(str(xml_b64))
        except Exception:
            continue
        docs.append(
            DocumentoAdn(
                nsu=nsu,
                chave_acesso=chave,
                tipo_documento=tipo,
                xml_bytes=xml_bytes,
            )
        )
    return docs


def sincronizar_empresa_adn(
    empresa: Empresa,
    *,
    thumbprint: str | None = None,
    competencia_filtro: str = "",
    nsu_inicial: int | None = None,
    max_lotes: int = MAX_LOTES_POR_SYNC,
) -> dict:
    """Baixa XMLs novos do ADN para a empresa (procuração) e grava no Sentinela."""
    cfg = carregar_config_certificado()
    thumb = (thumbprint or cfg.get("thumbprint") or "").strip().upper()
    session = _criar_sessao_mtls(thumb)

    cursor = int(nsu_inicial if nsu_inicial is not None else (empresa.nfse_ultimo_nsu or 0))
    importadas = 0
    ignoradas = 0
    eventos = 0
    erros: list[str] = []
    ultimo_nsu = cursor
    pasta_tmp = Path(tempfile.mkdtemp(prefix="adn_nfse_"))

    try:
        for _ in range(max(1, max_lotes)):
            dados = consultar_lote_dfe(session, cursor, empresa.cnpj)
            lote = parse_lote(dados)
            if not lote:
                break
            maior_nsu_lote = max((doc.nsu for doc in lote), default=cursor)
            for doc in lote:
                if doc.nsu > ultimo_nsu:
                    ultimo_nsu = doc.nsu
                tipo = (doc.tipo_documento or "").upper()
                if tipo and "NFSE" not in tipo and "DFE" not in tipo:
                    eventos += 1
                    continue
                comp = competencia_do_xml(doc.xml_bytes) or competencia_filtro
                if not comp:
                    ignoradas += 1
                    continue
                nome = (doc.chave_acesso or f"nsu{doc.nsu}") + ".xml"
                caminho = pasta_tmp / nome
                caminho.write_bytes(doc.xml_bytes)
                try:
                    lida = importar_xml_nfse(empresa, comp, caminho)
                except Exception as exc:
                    erros.append(f"NSU {doc.nsu}: {exc}")
                    ignoradas += 1
                    continue
                if lida is None:
                    ignoradas += 1
                else:
                    importadas += 1
            proximo = max(maior_nsu_lote, cursor) + 1
            if proximo <= cursor:
                break
            cursor = proximo
    finally:
        for arq in pasta_tmp.glob("*"):
            try:
                arq.unlink()
            except Exception:
                pass
        try:
            pasta_tmp.rmdir()
        except Exception:
            pass

    if ultimo_nsu != (empresa.nfse_ultimo_nsu or 0):
        empresa.nfse_ultimo_nsu = ultimo_nsu
        empresa.save(update_fields=["nfse_ultimo_nsu"])

    return {
        "ok": True,
        "importadas": importadas,
        "ignoradas": ignoradas,
        "eventos": eventos,
        "ultimo_nsu": ultimo_nsu,
        "erros": erros[:8],
    }
