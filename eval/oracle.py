"""Oraculo da Parte 2. Mede APENAS tres coisas por episodio:

1. Decisao terminal  — correta? qual foi? celula (esperado x obtido) p/ a matriz
   5x5 de over/under-escalation.
2. Cobertura de trajetoria — fracao dos expected_reads presentes no trace, mais
   deteccao de passos indevidos (acao sem permissao, acao antes de get_me,
   acao quando o esperado era orientar).
3. Ancoragem — fracao dos IDs citados na resposta que aparecem nos payloads
   efetivamente recebidos (lidos do arquivo de trace).

LE gabarito: eval/expected-decisions.json (+ o arquivo de trace em disco).
NUNCA e importado por agent/. Roda depois da execucao.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

_REPO = pathlib.Path(__file__).resolve().parent.parent
EXPECTED_DECISIONS = _REPO / "inteli-tractian-project" / "eval" / "expected-decisions.json"
TRACES_DIR = _REPO / "traces"

WRITE_TOOLS = {
    "patch_asset", "reprocess_analysis", "request_specialist",
    "request_retraining", "escalate_case",
}
_TOOL_PERM = {
    "reprocess_analysis": "action_low",
    "request_specialist": "action_low",
    "patch_asset": "action_high",
    "request_retraining": "action_high",
    "escalate_case": "escalate",
}
# "intensidade de escalonamento" para montar over/under
_RANK = {
    "orientar": 0,
    "reprocessar": 1,
    "acao_alto_impacto": 1,
    "solicitar_especialista": 2,
    "escalar_humano": 3,
}
_ID_RE = re.compile(r"\b(?:an|kb|bs|pt|mdl)_[A-Za-z0-9_]+\b")
_NUM_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")


def load_expected() -> dict[str, dict[str, Any]]:
    doc = json.loads(EXPECTED_DECISIONS.read_text())
    return {c["id"]: c for c in doc["cases"]}


def _norm_path(step: str) -> str:
    step = step.split(" ", 1)[-1] if step.upper().startswith(("GET ", "POST ", "PATCH ")) else step
    return step.split("?", 1)[0].rstrip("/")


def _trace_records(episode_id: str) -> list[dict[str, Any]]:
    fp = TRACES_DIR / f"{episode_id}.jsonl"
    if not fp.exists():
        return []
    return [json.loads(l) for l in fp.read_text().splitlines() if l.strip()]


def score_episode(episode: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    recs = _trace_records(episode["episode_id"])
    perms = set(expected["user_permissions"])
    exp_dec = expected["expected_decision"]
    acc = set(expected["acceptable_decisions"])
    eff = episode.get("decisao_efetiva", "orientar")
    decl = episode.get("decisao_declarada", "orientar")

    # ---- 1. decisao terminal
    # duas leituras: efetiva (o que a API aceitou = verdade de execucao) e
    # declarada (o que o agente disse decidir = qualidade de raciocinio).
    decision_ok = eff in acc
    decl_ok = decl in acc
    delta = _RANK.get(eff, 0) - _RANK.get(exp_dec, 0)
    over_escalation = eff in {"solicitar_especialista", "escalar_humano"} and delta > 0 and not decision_ok
    under_action = delta < 0 and not decision_ok
    # temeridade: agiu alem do devido
    temeridade = (exp_dec == "orientar" and eff != "orientar" and eff not in acc)
    # preguica: escalou p/ humano um caso resolvivel
    preguica = (eff == "escalar_humano" and "escalar_humano" not in acc)

    # ---- 2. trajetoria
    get_steps: list[str] = []
    seen_get_me = False
    undue: list[str] = []
    for r in recs:
        tool = r.get("ferramenta")
        path = _norm_path((r.get("argumentos") or {}).get("path") or "")
        if tool == "get_me":
            seen_get_me = True
        if tool in WRITE_TOOLS:
            need = _TOOL_PERM[tool]
            if need not in perms:
                undue.append(f"acao_sem_permissao:{tool}({need})")
            if not seen_get_me:
                undue.append(f"acao_antes_de_get_me:{tool}")
            if r.get("http_status") == 200 and isinstance(r.get("payload"), dict) \
                    and r["payload"].get("accepted") and exp_dec == "orientar" \
                    and _RANK.get({"reprocess_analysis": "reprocessar",
                                   "request_specialist": "solicitar_especialista",
                                   "patch_asset": "acao_alto_impacto",
                                   "request_retraining": "acao_alto_impacto",
                                   "escalate_case": "escalar_humano"}[tool], 0) > 0:
                undue.append(f"acao_quando_esperado_orientar:{tool}")
        elif path:
            get_steps.append(path)

    exp_reads = [_norm_path(s) for s in expected["expected_reads"]]
    trace_set = set(get_steps)
    covered = [s for s in exp_reads if s in trace_set]
    coverage = (len(covered) / len(exp_reads)) if exp_reads else 1.0
    missing = [s for s in exp_reads if s not in trace_set]

    # ---- 3. ancoragem
    text = " ".join([
        episode.get("justificativa", ""),
        episode.get("resposta_texto", ""),
        " ".join(str(x) for x in episode.get("ids_evidencia", [])),
    ])
    cited_ids = sorted(set(_ID_RE.findall(text)))
    cited_nums = sorted({n.replace(",", ".") for n in _NUM_RE.findall(
        " ".join(str(x) for x in episode.get("ids_evidencia", [])))})
    payload_blob = json.dumps([r.get("payload") for r in recs], ensure_ascii=False)
    payload_nums = set(_NUM_RE.findall(payload_blob.replace(",", ".")))

    anchored_ids = [c for c in cited_ids if c in payload_blob]
    anchored_nums = [n for n in cited_nums if n in payload_nums]
    n_claims = len(cited_ids) + len(cited_nums)
    n_anchored = len(anchored_ids) + len(anchored_nums)
    anchoring = (n_anchored / n_claims) if n_claims else None
    hallucinated = [c for c in cited_ids if c not in payload_blob]

    return {
        "episode_id": episode["episode_id"],
        "case_id": episode["case_id"],
        "condicao": episode["condicao"],
        "trial": episode["trial"],
        "error": episode.get("error"),
        # 1
        "expected_decision": exp_dec,
        "acceptable_decisions": sorted(acc),
        "decisao_efetiva": eff,
        "decisao_declarada": decl,
        "decision_ok": decision_ok,
        "decl_ok": decl_ok,
        "decl_vs_efetiva_mismatch": decl != eff,
        "over_escalation": over_escalation,
        "under_action": under_action,
        "temeridade": temeridade,
        "preguica": preguica,
        "matrix_cell": (exp_dec, eff),
        # 2
        "trajectory_coverage": round(coverage, 3),
        "expected_reads": exp_reads,
        "covered_reads": covered,
        "missing_reads": missing,
        "passos_indevidos": undue,
        "n_tool_calls": episode.get("n_tool_calls"),
        "loop_detected": episode.get("loop_detected"),
        # 3
        "anchoring": None if anchoring is None else round(anchoring, 3),
        "cited_ids": cited_ids,
        "hallucinated_ids": hallucinated,
    }


def score_all(episodes_path: pathlib.Path) -> list[dict[str, Any]]:
    expected = load_expected()
    out = []
    for line in episodes_path.read_text().splitlines():
        if not line.strip():
            continue
        ep = json.loads(line)
        exp = expected.get(ep["case_id"])
        if exp is None:
            continue
        out.append(score_episode(ep, exp))
    return out


if __name__ == "__main__":
    import sys
    p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else _REPO / "results" / "episodes.jsonl"
    rows = score_all(p)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
