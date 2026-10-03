#!/usr/bin/env bash
# Linux/macOS: ./iniciar_robo.sh  (reinicia sozinho se cair)
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
[ -f .env ] || { cp .env.example .env; echo "Edite o .env e rode de novo."; exit 1; }
while true; do
  .venv/bin/python run_bot.py
  echo "Robô parou; reiniciando em 30 s (Ctrl+C para sair)"; sleep 30
done
