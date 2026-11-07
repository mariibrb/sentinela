@echo off
cd /d "%~dp0sentinela_app"
echo Sentinela na rede do escritorio. Abra http://IP-DESTE-COMPUTADOR:8000/entrar/
python manage.py runserver 0.0.0.0:8000
