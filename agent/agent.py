"""Laco do agente: single-turn, tool-calling nativo, teto de 8 chamadas.

- Para quando o modelo emite a decisao final (sem tool_call), quando uma acao e
  aceita, ou no teto de 8.
- Detector de laco: mesma (ferramenta, argumentos) repetida -> corta.
- Saida final parseada com tolerancia (bloco json -> json cru -> regex).
"""
from __future__ import annotations

import json
import re
from typing import Any

from agent.client import ApiClient
from agent.llm import LLM
from agent.prompts import system_prompt
from agent.tools import TOOLS, WRITE_TOOLS, dispatch

import os

MAX_CALLS = 8          # teto duro do enunciado


def _call_budget() -> int:
    """Orcamento pratico de leituras antes de forcar a decisao final (economia de
    tokens no free tier). <= MAX_CALLS. Lido do env a cada episodio (apos load_env)."""
    return min(int(os.environ.get("AGENT_CALL_BUDGET", "5")), MAX_CALLS)

_ACTION_DECISION = {
    "reprocess_analysis": "reprocessar",
    "request_specialist": "solicitar_especialista",
    "patch_asset": "acao_alto_impacto",
    "request_retraining": "acao_alto_impacto",
    "escalate_case": "escalar_humano",
}

_DECISIONS = {"orientar", "reprocessar", "solicitar_especialista", "acao_alto_impacto", "escalar_humano"}

_EVIDENCE_RE = re.compile(r"\b(?:an|kb|bs|pt|mdl|asset|comp)_[A-Za-z0-9_]+\b")


def _user_message(case: dict) -> str:
    return (
        f"Chamado {case['ticket_id']} (case_id: {case['id']}).\n"
        f"Empresa: {case['company_id']}. Ativo: {case['asset_id']}. "
        f"Seu x-user-id: {case['user_id']}.\n"
        f"Mensagem do solicitante:\n\"\"\"\n{case['message']}\n\"\"\"\n"
        f"O ativo e o usuario ja estao identificados acima — va direto a eles, "
        f"nao liste a empresa inteira. Investigue e produza a decisao final no formato pedido."
    )


def _assistant_dict(msg: Any) -> dict[str, Any]:
    d = msg.model_dump(exclude_none=True)
    d.setdefault("role", "assistant")
    # nao reenvia chain-of-thought (gpt-oss/reasoning) no historico
    for k in ("reasoning", "reasoning_content"):
        d.pop(k, None)
    if d.get("tool_calls") and "content" not in d:
        d["content"] = ""
    return d


def _rms_digest(data: dict[str, Any]) -> dict[str, Any]:
    """rms: descarta a serie (fica no trace), mantem os agregados uteis."""
    out = {k: v for k, v in data.items() if k != "samples"}
    s = data.get("samples") or []
    vals = [p.get("value") for p in s if isinstance(p, dict) and isinstance(p.get("value"), (int, float))]
    if s:
        out["samples_resumo"] = {
            "n": len(s),
            "primeiro_ts": s[0].get("ts") if isinstance(s[0], dict) else None,
            "ultimo_ts": s[-1].get("ts") if isinstance(s[-1], dict) else None,
            "ultimo_valor": vals[-1] if vals else None,
            "min": min(vals) if vals else None,
            "max": max(vals) if vals else None,
            "ultimos_5": vals[-5:],
        }
    return out


def _slim(value: Any, key: str | None = None) -> Any:
    """Encolhe o que vai ao CONTEXTO do modelo. O trace guarda o payload cheio."""
    if isinstance(value, dict):
        if "samples" in value and "unit" in value:      # payload de rms
            value = _rms_digest(value)
        return {k: _slim(v, k) for k, v in value.items()}
    if isinstance(value, list):
        limit = 5 if key in {"peaks", "results", "analyses", "assets", "bands_missing"} else 12
        if len(value) > limit:
            kept = [_slim(x) for x in value[:limit]]
            return kept + [f"...(+{len(value) - limit} omitidos; ver trace)..."]
        return [_slim(x) for x in value]
    if isinstance(value, str) and len(value) > 900 and key != "body":
        return value[:900] + " …(truncado)"
    return value


def _tool_result_content(result: dict[str, Any]) -> str:
    return json.dumps(
        {
            "http_status": result.get("http_status"),
            "mode": result.get("mode"),
            "notes": result.get("notes"),
            "data": _slim(result.get("data")),
        },
        ensure_ascii=False,
    )


def _compress_old_tool_msgs(messages: list[dict[str, Any]], keep_last: int = 3) -> None:
    """Encolhe resultados de ferramenta antigos (ja "digeridos") p/ poupar TPM.

    Mantem os `keep_last` mais recentes intactos; os anteriores viram um resumo
    curto (o payload cheio continua no trace em disco).
    """
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    for i in tool_idxs[:-keep_last]:
        c = messages[i].get("content") or ""
        if len(c) > 220 and not c.startswith("{\"resumo\""):
            messages[i]["content"] = json.dumps(
                {"resumo": c[:200] + " …(comprimido; ver trace)"}, ensure_ascii=False
            )


def _normalize_decision(raw: str | None) -> str:
    if not raw:
        return "orientar"
    s = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if s in _DECISIONS:
        return s
    if "reprocess" in s:
        return "reprocessar"
    if "especial" in s or "specialist" in s:
        return "solicitar_especialista"
    if "retrain" in s or "retrein" in s or "alto_impacto" in s or "patch" in s or "criticidade" in s:
        return "acao_alto_impacto"
    if "escal" in s or "humano" in s or "human" in s:
        return "escalar_humano"
    if "orient" in s or "advise" in s:
        return "orientar"
    return "orientar"


def parse_final(text: str) -> dict[str, Any]:
    """Extrai o JSON final com tolerancia. Sempre devolve as 4 chaves."""
    obj: dict[str, Any] | None = None
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = list(blocks)
    # tambem tenta o maior {...} solto
    brace = re.findall(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.DOTALL)
    candidates += sorted(brace, key=len, reverse=True)
    for cand in candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict) and "decisao_terminal" in parsed:
                obj = parsed
                break
        except Exception:
            continue

    if obj is not None:
        ids = obj.get("ids_evidencia") or []
        if isinstance(ids, str):
            ids = [ids]
        return {
            "decisao_terminal": _normalize_decision(str(obj.get("decisao_terminal", ""))),
            "ids_evidencia": [str(x) for x in ids],
            "justificativa": str(obj.get("justificativa", "")).strip(),
            "resposta_texto": str(obj.get("resposta_texto", "")).strip() or text.strip(),
            "parse": "json",
        }

    # fallback por regex
    m = re.search(r"decis[aã]o[_ ]?terminal\D+([a-z_]+)", text, re.IGNORECASE)
    return {
        "decisao_terminal": _normalize_decision(m.group(1) if m else None),
        "ids_evidencia": sorted(set(_EVIDENCE_RE.findall(text))),
        "justificativa": "",
        "resposta_texto": text.strip(),
        "parse": "regex",
    }


def _effective_decision(calls: list[dict[str, Any]]) -> str:
    """O que o agente DE FATO fez, lido do trace (ultima acao aceita)."""
    decision = "orientar"
    for c in calls:
        if c["ferramenta"] in _ACTION_DECISION and c.get("http_status") == 200:
            raw = c.get("raw") or {}
            if isinstance(raw, dict) and raw.get("accepted"):
                decision = _ACTION_DECISION[c["ferramenta"]]
    return decision


def _attempted_actions(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in calls:
        if c["ferramenta"] in WRITE_TOOLS:
            out.append({
                "ferramenta": c["ferramenta"],
                "http_status": c.get("http_status"),
                "aceito": bool((c.get("raw") or {}).get("accepted")) if isinstance(c.get("raw"), dict) else False,
            })
    return out


def run_episode(
    case: dict,
    condicao: str,
    trial: int,
    seed: str,
    base_url: str,
    llm: LLM,
) -> dict[str, Any]:
    episode_id = f"{case['id']}__{condicao}__t{trial}"
    ctx = {"episode_id": episode_id, "case_id": case["id"], "condicao": condicao, "trial": trial}
    client = ApiClient(base_url, user_id=case["user_id"], seed=seed, episode_ctx=ctx)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt(condicao)},
        {"role": "user", "content": _user_message(case)},
    ]

    calls_made = 0
    budget = _call_budget()
    seen_sigs: set[str] = set()
    loop_detected = False
    stop_reason = "final_text"
    error: str | None = None

    try:
        while calls_made < MAX_CALLS:
            _compress_old_tool_msgs(messages, keep_last=2)
            resp = llm.complete(messages, tools=TOOLS, tool_choice="auto", max_tokens=900)
            msg = resp.choices[0].message
            messages.append(_assistant_dict(msg))

            if not msg.tool_calls:
                stop_reason = "final_text"
                break

            hit_loop = False
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                sig = name + "::" + json.dumps(args, sort_keys=True, ensure_ascii=False)
                if sig in seen_sigs:
                    loop_detected = hit_loop = True
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id,
                        "content": json.dumps({
                            "erro": "loop_detector: chamada identica repetida. "
                                    "Pare de investigar e emita a decisao final agora."}),
                    })
                    continue
                seen_sigs.add(sig)
                calls_made += 1
                result = dispatch(client, name, args)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": _tool_result_content(result),
                })

            if hit_loop:
                stop_reason = "loop_detected"
                break
            if calls_made >= budget:
                stop_reason = "call_budget"
                break

        # garantir uma resposta final em texto — SEM ferramentas (evita hang em
        # provedores que travam apos varios rounds de tool-call)
        final_text = ""
        if messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            final_text = messages[-1].get("content") or ""
        if "decisao_terminal" not in final_text:
            _compress_old_tool_msgs(messages, keep_last=3)
            messages.append({
                "role": "user",
                "content": "Encerre agora: emita SOMENTE a decisao final em um bloco "
                           "```json com as chaves decisao_terminal, ids_evidencia, "
                           "justificativa, resposta_texto. Nao chame ferramentas.",
            })
            resp = llm.complete(messages, tools=None, max_tokens=1000)
            final_text = resp.choices[0].message.content or ""
            messages.append({"role": "assistant", "content": final_text})
    except Exception as exc:  # nao trava o runner
        error = repr(exc)
        final_text = ""

    parsed = parse_final(final_text) if final_text else {
        "decisao_terminal": "orientar", "ids_evidencia": [], "justificativa": "",
        "resposta_texto": "", "parse": "none",
    }
    effective = _effective_decision(client.calls)

    episode = {
        "episode_id": episode_id,
        "case_id": case["id"],
        "ticket_id": case["ticket_id"],
        "asset_id": case["asset_id"],
        "user_id": case["user_id"],
        "condicao": condicao,
        "trial": trial,
        "seed": seed,
        "model": llm.model,
        "provider": llm.provider,
        "temperature": llm.temperature,
        "n_tool_calls": calls_made,
        "loop_detected": loop_detected,
        "stop_reason": stop_reason,
        "error": error,
        "decisao_declarada": parsed["decisao_terminal"],
        "decisao_efetiva": effective,
        "ids_evidencia": parsed["ids_evidencia"],
        "justificativa": parsed["justificativa"],
        "resposta_texto": parsed["resposta_texto"],
        "parse_mode": parsed["parse"],
        "raw_final": final_text,
        "acoes_tentadas": _attempted_actions(client.calls),
        "trace": [
            {"ordinal": i + 1, "ferramenta": c["ferramenta"],
             "path": c["argumentos"].get("path"), "params": c["argumentos"].get("params"),
             "http_status": c.get("http_status"), "mode": c.get("mode")}
            for i, c in enumerate(client.calls)
        ],
        "trace_file": str(client.trace_path.relative_to(client.trace_path.parent.parent)),
    }
    client.close()
    return episode
