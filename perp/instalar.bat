@echo off
cd /d %~dp0
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Instalado. Edite o arquivo .env e depois rode:  .venv\Scripts\python.exe run_bot.py --check
pause
