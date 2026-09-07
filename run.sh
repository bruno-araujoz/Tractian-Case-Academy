#!/usr/bin/env bash
# Orquestrador do projeto. Alvos: setup | experiment | analyze
set -euo pipefail
cd "$(dirname "$0")"

VENV=.venv
PY="$VENV/bin/python"
API_DIR=inteli-tractian-project/api
API_URL=http://127.0.0.1:8000

setup() {
  [ -d "$VENV" ] || python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -e "$API_DIR[dev]"
  "$VENV/bin/pip" install -q -r requirements.txt
  ( cd "$API_DIR" && ../../"$PY" -m seed_data && ../../"$PY" -m package_material )
  mkdir -p .run
  if ! curl -sf -o /dev/null "$API_URL/docs"; then
    echo "subindo a API em $API_URL ..."
    ( cd "$API_DIR" && nohup ../../"$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
        > ../../.run/api.log 2>&1 & echo $! > ../../.run/api.pid )
    for _ in $(seq 1 20); do curl -sf -o /dev/null "$API_URL/docs" && break; sleep 0.5; done
  fi
  curl -sf -o /dev/null "$API_URL/docs" && echo "API OK ($API_URL/docs)"
  [ -f .env ] || { echo "!! copie .env.example para .env e preencha a chave"; exit 1; }
}

experiment() {
  curl -sf -o /dev/null "$API_URL/docs" || { echo "API fora do ar; rode: bash run.sh setup"; exit 1; }
  "$PY" -m eval.runner "${@:-}"
}

analyze() {
  "$PY" -m eval.analyze
  echo
  echo "-> results/summary_A_vs_B.md  |  results/comparison.png  |  results/scores.csv"
}

case "${1:-}" in
  setup) setup ;;
  experiment) shift; experiment "$@" ;;
  analyze) analyze ;;
  *) echo "uso: bash run.sh {setup|experiment [--trials N]|analyze}"; exit 1 ;;
esac
