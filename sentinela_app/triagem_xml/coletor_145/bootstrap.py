from __future__ import annotations

import os
import sys
from pathlib import Path

NOME_FERRAMENTA = "Coletor"


def raiz_app() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def pasta_dados() -> Path:
    env = (os.environ.get("COLETOR_RESOLVED_DATA_ROOT") or "").strip()
    if env:
        return Path(env)
    if getattr(sys, "frozen", False):
        return raiz_app() / "dados"
    return raiz_app() / "_dados"


def aplicar_temp_fora_c() -> Path:
    root = raiz_app()
    if getattr(sys, "frozen", False):
        os.environ["COLETOR_DATA_ROOT"] = str(root / "dados")

    shared = root.parent / "_shared"
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    try:
        from forcar_temp_fora_c import aplicar

        dados = aplicar(NOME_FERRAMENTA, root, env_var="COLETOR_DATA_ROOT")
    except Exception:
        dados = pasta_dados()
        tmp = dados / "temp_windows_ambiente"
        tmp.mkdir(parents=True, exist_ok=True)
        s = str(tmp)
        os.environ["TEMP"] = s
        os.environ["TMP"] = s
        os.environ["TMPDIR"] = s
        os.environ["COLETOR_RESOLVED_DATA_ROOT"] = str(dados)

    browsers = dados / "ms-playwright"
    browsers.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
    os.environ.setdefault("PIP_CACHE_DIR", str(dados / "pip_cache"))
    (dados / "planilhas").mkdir(parents=True, exist_ok=True)
    (dados / "chrome_perfil").mkdir(parents=True, exist_ok=True)
    return dados
