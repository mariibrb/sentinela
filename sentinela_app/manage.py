#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from pathlib import Path


def _configurar_caminhos():
    base = str(Path(__file__).resolve().parent)
    if base in sys.path:
        sys.path.remove(base)
    sys.path.insert(0, base)
    for extra in (
        r"D:\App Mari\ColetorNovo\pacote\_internal",
        r"D:\App Mari\Ceifador\runtime\Lib\site-packages",
        r"D:\App Mari\Detetive Fiscal\runtime\Lib\site-packages",
    ):
        if os.path.isdir(extra) and extra not in sys.path:
            sys.path.append(extra)


def main():
    """Run administrative tasks."""
    _configurar_caminhos()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sentinela.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
