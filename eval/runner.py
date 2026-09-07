"""Runner do experimento: 17 casos x {A,B} x N trials.

- seed FIXA por caso (default "complete": overrides de seed.json seguem valendo,
  o resto vira complete — condicao de cenario limpa e deterministica).
- nao trava se um episodio falhar (registra error e segue).
- escreve results/episodes.jsonl (uma linha por episodio) e results/run_config.json.
- retomavel: pula episodios ja presentes no jsonl (por episode_id).
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from agent.agent import run_episode
from agent.dataset import load_cases
from agent.llm import LLM

_REPO = pathlib.Path(__file__).resolve().parent.parent
RESULTS = _REPO / "results"

# seed fixa por caso. Default global "complete"; da p/ sobrepor um caso aqui.
SEED_BY_CASE: dict[str, str] = {}
DEFAULT_SEED = "complete"


def _done_ids(path: pathlib.Path) -> set[str]:
    if not path.exists():
        return set()
    ids = set()
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                ids.add(json.loads(line)["episode_id"])
            except Exception:
                pass
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--conditions", default="A,B")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--out", default=str(RESULTS / "episodes.jsonl"))
    ap.add_argument("--only", default=None, help="csv de case_ids p/ rodar so eles")
    args = ap.parse_args()

    RESULTS.mkdir(exist_ok=True)
    out_path = pathlib.Path(args.out)
    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    cases = load_cases()
    if args.only:
        want = {c.strip() for c in args.only.split(",")}
        cases = [c for c in cases if c["id"] in want or c["ticket_id"] in want]

    import os as _os
    llm = LLM()
    cfg = {
        "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "provider": llm.provider,
        "model": llm.model,
        "temperature": llm.temperature,
        "agent_call_budget": int(_os.environ.get("AGENT_CALL_BUDGET", "5")),
        "n_chaves_llm": len(llm._keys),
        "trials": args.trials,
        "conditions": conditions,
        "n_casos": len(cases),
        "seed_default": DEFAULT_SEED,
        "seed_by_case": SEED_BY_CASE,
        "condicao_A": "prompt base",
        "condicao_B": "prompt base + clausula de verificacao de baseline.state/detection_mode",
        "hipotese_H1": "impor verificacao do estado do baseline antes de aceitar um insight "
                       "reduz decisoes temerarias sem aumento proporcional de escalonamento indevido",
    }
    (RESULTS / "run_config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2))

    done = _done_ids(out_path)
    total = len(cases) * len(conditions) * args.trials
    n = 0
    t0 = time.time()
    # ordem: trial -> condicao -> caso, para que um corte precoce ainda tenha
    # cobertura COMPLETA em profundidade de trials menor.
    with out_path.open("a", encoding="utf-8") as fh:
        for trial in range(1, args.trials + 1):
            for cond in conditions:
                for case in cases:
                    seed = SEED_BY_CASE.get(case["id"], DEFAULT_SEED)
                    n += 1
                    ep_id = f"{case['id']}__{cond}__t{trial}"
                    if ep_id in done:
                        print(f"[{n}/{total}] skip {ep_id} (ja feito)")
                        continue
                    tag = f"[{n}/{total}] {ep_id}"
                    el = (time.time() - t0) / 60
                    print(f"{tag}  (elapsed {el:.1f}min)", file=sys.stderr, flush=True)
                    try:
                        ep = run_episode(case, cond, trial, seed, args.base_url, llm)
                    except Exception as exc:  # blindagem extra
                        ep = {
                            "episode_id": ep_id, "case_id": case["id"],
                            "ticket_id": case["ticket_id"], "asset_id": case["asset_id"],
                            "user_id": case["user_id"], "condicao": cond, "trial": trial,
                            "seed": seed, "model": llm.model, "provider": llm.provider,
                            "temperature": llm.temperature, "n_tool_calls": 0,
                            "loop_detected": False, "stop_reason": "crash", "error": repr(exc),
                            "decisao_declarada": "orientar", "decisao_efetiva": "orientar",
                            "ids_evidencia": [], "justificativa": "", "resposta_texto": "",
                            "parse_mode": "none", "raw_final": "", "acoes_tentadas": [], "trace": [],
                        }
                    fh.write(json.dumps(ep, ensure_ascii=False) + "\n")
                    fh.flush()
                    print(f"{tag} -> decl={ep['decisao_declarada']} efet={ep['decisao_efetiva']} "
                          f"calls={ep['n_tool_calls']} stop={ep.get('stop_reason')} err={ep.get('error')}")

    dt = time.time() - t0
    print(f"\nconcluido: {n} episodios em {dt/60:.1f} min "
          f"(tokens prompt~{llm.total_prompt_tokens}, compl~{llm.total_completion_tokens}, "
          f"reqs={llm.n_requests})")


if __name__ == "__main__":
    main()
