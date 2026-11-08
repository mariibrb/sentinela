"""Liga o motor do Garimpeiro à triagem sem substituir o arquivo dele."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
import types
from pathlib import Path

PASTA_ESCRITORIO = Path(
    r"C:\Users\bjmar\OneDrive - NASCEL CONTABILIDADE LTDA\Nascel Contabilidade - escritafiscal\escritafiscal\Garimpeiro"
)
_MOTOR = None
_TRAVA = threading.Lock()


class _Sessao(dict):
    def __getattr__(self, chave):
        try:
            return self[chave]
        except KeyError as erro:
            raise AttributeError(chave) from erro

    def __setattr__(self, chave, valor):
        self[chave] = valor


class _Mudo:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __call__(self, *args, **kwargs):
        return self

    def __getattr__(self, nome):
        return self

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False


def _fragmento(funcao=None, **kwargs):
    if callable(funcao):
        return funcao

    def _embrulho(alvo):
        return alvo

    return _embrulho


def _instalar_streamlit_falso() -> None:
    modulo = types.ModuleType("streamlit")
    modulo.session_state = _Sessao()
    modulo.fragment = _fragmento

    def _pegar(nome):
        if nome == "fragment":
            return _fragmento

        def _chamar(*args, **kwargs):
            if nome == "columns":
                pedido = args[0] if args else 1
                quantidade = pedido if isinstance(pedido, int) else len(pedido)
                return tuple(_Mudo() for _ in range(max(int(quantidade), 1)))
            if nome in {"spinner", "status", "expander", "container", "sidebar", "form", "empty", "popover"}:
                return _Mudo()
            return _Mudo()

        return _chamar

    modulo.__getattr__ = _pegar
    componentes = types.ModuleType("streamlit.components")
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *args, **kwargs: None
    componentes.v1 = v1
    sys.modules["streamlit"] = modulo
    sys.modules["streamlit.components"] = componentes
    sys.modules["streamlit.components.v1"] = v1


def _em_teste() -> bool:
    return any(parte == "test" for parte in sys.argv)


def pasta_resultados() -> Path:
    """Pasta em que o arquivo, a autenticidade e o fechamento já vivem."""
    env = (os.environ.get("GARIMPEIRO_DATA_ROOT") or "").strip().strip('"').strip("'")
    if _em_teste():
        if env and "onedrive" not in env.lower() and "escritafiscal" not in env.lower():
            pasta = Path(env)
        else:
            pasta = Path(tempfile.gettempdir()) / "sentinela_garimpeiro_teste"
    elif env:
        pasta = Path(env)
    elif PASTA_ESCRITORIO.is_dir():
        pasta = PASTA_ESCRITORIO
    else:
        from django.conf import settings

        pasta = Path(settings.MEDIA_ROOT) / "garimpeiro"
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _apontar(motor) -> None:
    import streamlit as st

    pasta = pasta_resultados()
    os.environ["GARIMPEIRO_DATA_ROOT"] = str(pasta)
    raiz = pasta / "ArquivoFiscal"
    raiz.mkdir(parents=True, exist_ok=True)
    st.session_state[motor.SESSION_KEY_ARQUIVO_FISCAL_ROOT] = str(raiz)
    st.session_state[motor.SESSION_KEY_ARQUIVO_FISCAL_CAPTURA] = True
    os.environ["GARIMPEIRO_ARQUIVO_FISCAL_ROOT"] = str(raiz)
    try:
        import core.exportacoes as exportacoes

        exportacoes._PASTA_DADOS_CACHE = None
    except Exception:
        pass
    for chave in list(st.session_state.keys()):
        if str(chave).startswith("_arquivo_fiscal_dir_resolvido_"):
            st.session_state.pop(chave, None)


def carregar():
    global _MOTOR
    with _TRAVA:
        if _MOTOR is None:
            os.environ["GARIMPEIRO_HEADLESS"] = "1"
            os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
            os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
            os.environ["GARIMPEIRO_INSTALL_ROOT"] = str(Path(__file__).resolve().parent / "motor")
            os.environ["GARIMPEIRO_DATA_ROOT"] = str(pasta_resultados())
            _instalar_streamlit_falso()
            motor_dir = Path(__file__).resolve().parent / "motor"
            if str(motor_dir) not in sys.path:
                sys.path.insert(0, str(motor_dir))
            app_py = motor_dir / "app.py"
            spec = importlib.util.spec_from_file_location("garimpeiro_motor_app", app_py)
            if spec is None or spec.loader is None:
                raise RuntimeError("O motor do Garimpeiro não está no Sentinela.")
            modulo = importlib.util.module_from_spec(spec)
            sys.modules["garimpeiro_motor_app"] = modulo
            spec.loader.exec_module(modulo)
            _MOTOR = modulo
        _apontar(_MOTOR)
        return _MOTOR


def processar_garimpo(cnpj: str, caminhos) -> tuple[bool, str, int]:
    """Lê o lote com o motor original e devolve a frase de resultado dele."""
    try:
        motor = carregar()
        from core.garimpo import executar_garimpo

        ok, mensagem, quantidade, _meses = executar_garimpo(
            motor,
            cnpj,
            [Path(caminho) for caminho in caminhos],
        )
        return bool(ok), str(mensagem), int(quantidade or 0)
    except Exception as erro:
        return False, f"O Garimpeiro não concluiu esta leitura. {erro}", 0
