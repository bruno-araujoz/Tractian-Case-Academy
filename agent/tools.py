"""As 18 ferramentas 1:1 com os endpoints da API.

Schemas (o unico que o modelo enxerga) sao descricoes mecanicas do contrato:
verbo, recurso, parametros e permissao exigida. Nenhuma pista de gabarito.
`dispatch()` traduz (nome, args) numa chamada do ApiClient (que ja faz trace).
"""
from __future__ import annotations

from typing import Any, Callable

from agent.client import ApiClient

_S = {"type": "string"}
_JUST = {"type": "string"}  # justification: >=20 chars apos strip, senao 400


def _tool(name: str, desc: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


# descricoes minimas (economia de tokens no free tier); a politica esta no system prompt
TOOLS: list[dict[str, Any]] = [
    _tool("get_company", "empresa: dados cadastrais", {"company_id": _S}, ["company_id"]),
    _tool("list_company_assets", "lista ativos da empresa", {"company_id": _S}, ["company_id"]),
    _tool("get_me", "seu usuario + permissoes [read,action_low,action_high,escalate]; chame antes de agir", {}, []),
    _tool("get_asset", "config tecnica do ativo + pontos", {"asset_id": _S}, ["asset_id"]),
    _tool("patch_asset", "altera config do ativo (ex. criticality). perm action_high",
          {"asset_id": _S, "justification": _JUST, "changes": {"type": "object"}}, ["asset_id", "justification"]),
    _tool("list_asset_analyses", "analises do ativo; status opc [current,stale,pending,inconclusive]",
          {"asset_id": _S, "status": _S}, ["asset_id"]),
    _tool("get_analysis", "detalhe da analise (type,severity,confidence,detection_mode,evidence,limitations,status)",
          {"analysis_id": _S}, ["analysis_id"]),
    _tool("reprocess_analysis", "reprocessa a analise. perm action_low",
          {"analysis_id": _S, "justification": _JUST}, ["analysis_id", "justification"]),
    _tool("request_specialist", "solicita analise especializada interna. perm action_low",
          {"analysis_id": _S, "justification": _JUST}, ["analysis_id", "justification"]),
    _tool("get_baseline", "baseline: state[learning|established|invalidated], detection_mode[baseline|symptom], features",
          {"asset_id": _S, "point_id": _S}, ["asset_id"]),
    _tool("get_rms", "serie RMS mm/s + baseline_reference, baseline_state, alarm_threshold",
          {"asset_id": _S, "point_id": _S}, ["asset_id"]),
    _tool("get_spectrum", "espectro: peaks e bands_missing", {"asset_id": _S, "point_id": _S}, ["asset_id"]),
    _tool("get_data_quality", "completeness, freshness_minutes, snr_db, staleness_flag",
          {"asset_id": _S, "point_id": _S}, ["asset_id"]),
    _tool("get_model", "modelo: version, coverage, requirements(min_completeness/min_snr_db/min_rotation_rpm), processing_state",
          {"model_id": _S}, ["model_id"]),
    _tool("request_retraining", "solicita retreinamento do modelo. perm action_high",
          {"model_id": _S, "justification": _JUST}, ["model_id", "justification"]),
    _tool("search_knowledge", "busca na base (substring em titulo/corpo). type opc [procedure,glossary,guidance]",
          {"q": _S, "type": _S}, ["q"]),
    _tool("get_knowledge_doc", "corpo completo de um doc (ex. kb_proc_001)", {"doc_id": _S}, ["doc_id"]),
    _tool("escalate_case", "escala o chamado p/ analise humana de campo. perm escalate",
          {"case_id": _S, "justification": _JUST}, ["case_id", "justification"]),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]

# ------------------------------------------------------------------ dispatch

WRITE_TOOLS = {
    "patch_asset", "reprocess_analysis", "request_specialist",
    "request_retraining", "escalate_case",
}


def _body(args: dict[str, Any], extra: tuple[str, ...] = ()) -> dict[str, Any]:
    body: dict[str, Any] = {"justification": args.get("justification", "")}
    for k in extra:
        if args.get(k) is not None:
            body[k] = args[k]
    return body


def _params(args: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: args[k] for k in keys if args.get(k) not in (None, "")}


_SPEC: dict[str, dict[str, Any]] = {
    "get_company": {"m": "GET", "p": lambda a: f"/companies/{a['company_id']}"},
    "list_company_assets": {"m": "GET", "p": lambda a: f"/companies/{a['company_id']}/assets"},
    "get_me": {"m": "ME", "p": lambda a: "/users/me"},
    "get_asset": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}"},
    "patch_asset": {"m": "PATCH", "p": lambda a: f"/assets/{a['asset_id']}",
                    "b": lambda a: _body(a, ("changes",))},
    "list_asset_analyses": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}/analyses",
                            "q": ("status",)},
    "get_analysis": {"m": "GET", "p": lambda a: f"/analyses/{a['analysis_id']}"},
    "reprocess_analysis": {"m": "POST", "p": lambda a: f"/analyses/{a['analysis_id']}/reprocess",
                           "b": _body},
    "request_specialist": {"m": "POST", "p": lambda a: f"/analyses/{a['analysis_id']}/request-specialist",
                           "b": _body},
    "get_baseline": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}/baseline", "q": ("point_id",)},
    "get_rms": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}/rms", "q": ("point_id",)},
    "get_spectrum": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}/spectrum", "q": ("point_id",)},
    "get_data_quality": {"m": "GET", "p": lambda a: f"/assets/{a['asset_id']}/data-quality", "q": ("point_id",)},
    "get_model": {"m": "GET", "p": lambda a: f"/models/{a['model_id']}"},
    "request_retraining": {"m": "POST", "p": lambda a: f"/models/{a['model_id']}/request-retraining",
                           "b": _body},
    "search_knowledge": {"m": "GET", "p": lambda a: "/knowledge/search", "q": ("q", "type")},
    "get_knowledge_doc": {"m": "GET", "p": lambda a: f"/knowledge/{a['doc_id']}"},
    "escalate_case": {"m": "POST", "p": lambda a: f"/cases/{a['case_id']}/escalate", "b": _body},
}


def dispatch(client: ApiClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    spec = _SPEC.get(name)
    if spec is None:
        return {"http_status": None, "mode": "error",
                "notes": f"ferramenta desconhecida: {name}", "data": None, "raw": None}
    try:
        path = spec["p"](args)
    except KeyError as exc:
        return {"http_status": None, "mode": "error",
                "notes": f"argumento obrigatorio ausente: {exc}", "data": None, "raw": None}

    method = spec["m"]
    if method == "ME":
        return client.get_me(name)
    if method == "GET":
        return client.get(name, path, params=_params(args, spec.get("q", ())))
    body: Callable[[dict[str, Any]], dict[str, Any]] = spec["b"]
    if method == "POST":
        return client.post(name, path, body(args))
    return client.patch(name, path, body(args))
