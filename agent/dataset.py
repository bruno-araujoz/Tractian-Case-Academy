"""Carrega os casos que o agente PODE ver: agent-input/cases.json.

So os 6 campos de input (id, ticket_id, company_id, user_id, asset_id, message).
Nao le nenhuma fonte de gabarito (nem a tabela bruta de casos, nem a pasta do
avaliador).
"""
from __future__ import annotations

import json
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent
AGENT_INPUT = _REPO / "inteli-tractian-project" / "agent-input" / "cases.json"


def load_cases() -> list[dict]:
    return json.loads(AGENT_INPUT.read_text())


def get_case(case_id: str) -> dict:
    for c in load_cases():
        if c["id"] == case_id or c["ticket_id"] == case_id:
            return c
    raise KeyError(f"caso nao encontrado em agent-input/cases.json: {case_id}")
