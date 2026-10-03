@echo off
REM Inicia o robô no Windows. Na primeira vez rode instalar.bat
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  echo Rode instalar.bat primeiro.
  pause
  exit /b 1
)
:loop
.venv\Scripts\python.exe run_bot.py
echo O robo parou. Reiniciando em 30 segundos (feche a janela para encerrar)...
timeout /t 30
goto loop
