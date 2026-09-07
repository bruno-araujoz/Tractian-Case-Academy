"""CLI: roda 1 caso.

  python -m agent.run_case --case case_tkt_inv_04 --condition A --trial 1
  python -m agent.run_case --case TKT-CTX-01 --condition B         # ticket_id tambem serve

seed default = case_id (fixa por caso). Imprime o resumo do episodio.
"""
from __future__ import annotations

import argparse
import json

from agent.agent import run_episode
from agent.dataset import get_case
from agent.llm import LLM


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True)
    ap.add_argument("--condition", default="A", choices=["A", "B"])
    ap.add_argument("--trial", type=int, default=1)
    ap.add_argument("--seed", default=None,
                    help="default: 'complete' (fixo p/ todos; overrides de seed.json seguem valendo)")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--json", action="store_true", help="imprime o episodio completo em JSON")
    args = ap.parse_args()

    case = get_case(args.case)
    seed = args.seed or "complete"
    llm = LLM()
    ep = run_episode(case, args.condition, args.trial, seed, args.base_url, llm)

    if args.json:
        print(json.dumps(ep, ensure_ascii=False, indent=2))
        return

    print(f"=== {ep['episode_id']}  (ativo {ep['asset_id']}, user {ep['user_id']}, seed {seed}) ===")
    print(f"chamadas: {ep['n_tool_calls']}  | stop: {ep['stop_reason']}  | loop: {ep['loop_detected']}"
          f"  | parse: {ep['parse_mode']}  | erro: {ep['error']}")
    print("trajetoria:")
    for step in ep["trace"]:
        print(f"  {step['ordinal']:>2}. {step['ferramenta']:<22} {step['path'] or ''}"
              f"  -> {step['http_status']} {step['mode']}")
    print(f"acoes tentadas: {ep['acoes_tentadas'] or '(nenhuma)'}")
    print(f"decisao declarada: {ep['decisao_declarada']}   | decisao efetiva (trace): {ep['decisao_efetiva']}")
    print(f"ids_evidencia: {ep['ids_evidencia']}")
    print(f"justificativa: {ep['justificativa']}")
    print(f"resposta_texto:\n{ep['resposta_texto']}")


if __name__ == "__main__":
    main()
