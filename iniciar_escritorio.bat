@echo off
cd /d "%~dp0sentinela_app"
set "PY_RUNTIME=D:\App Mari\Oraculo\runtime\python.exe"
if exist "%PY_RUNTIME%" (
    echo Sentinela + Apanhador NFS-e 1.4.5 na rede do escritorio. Abra http://127.0.0.1:8000/entrar/
    "%PY_RUNTIME%" manage.py migrate
    "%PY_RUNTIME%" manage.py preparar_ambiente
    "%PY_RUNTIME%" manage.py runserver 0.0.0.0:8000
) else (
    echo Sentinela + Apanhador NFS-e 1.4.5 na rede do escritorio. Abra http://127.0.0.1:8000/entrar/
    python manage.py migrate
    python manage.py runserver 0.0.0.0:8000
)
