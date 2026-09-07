"""Cliente HTTP fino para a Tractian Industrial Support API + gravacao de trace.

Responsabilidades (Bloco 1):
- injetar o header `x-user-id`;
- injetar o parametro `seed` em todo GET;
- normalizar `GET /users/me` (objeto cru) para a mesma forma do envelope;
- tolerar tanto o erro {code,message} quanto o 422 {detail:[...]} do FastAPI;
- gravar uma linha JSONL por chamada desde a primeira, com:
  episode_id, case_id, condicao, trial, ordinal, ferramenta, argumentos,
  mode retornado, payload retornado, latencia.

Sem dependencia de SDK de LLM. Usado tanto pelo agente quanto pelos smokes.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import time
from typing import Any

import httpx

_REPO = pathlib.Path(__file__).resolve().parent.parent
TRACE_DIR = _REPO / "traces"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class ApiClient:
    def __init__(
        self,
        base_url: str,
        user_id: str | None,
        seed: str | None,
        episode_ctx: dict[str, Any],
        trace_path: pathlib.Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.seed = seed
        # episode_ctx: {episode_id, case_id, condicao, trial}
        self.ctx = episode_ctx
        self._ordinal = 0
        self._http = httpx.Client(timeout=timeout)
        # tudo que a API devolveu (campo `data` normalizado) -> ancoragem no oraculo
        self.received: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.trace_path = trace_path or (TRACE_DIR / f"{self.ctx['episode_id']}.jsonl")
        self.trace_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers
    def _headers(self) -> dict[str, str]:
        return {"x-user-id": self.user_id} if self.user_id else {}

    @staticmethod
    def _normalize(status: int, body: Any) -> tuple[str, Any, Any]:
        """Devolve (mode, notes, data) unificando envelope / objeto cru / erro."""
        if isinstance(body, dict) and "mode" in body and "data" in body:
            return body.get("mode"), body.get("notes"), body.get("data")
        if status >= 400:
            if isinstance(body, dict) and "code" in body:  # {code,message}
                return "error", body.get("message"), body
            if isinstance(body, dict) and "detail" in body:  # 422 FastAPI
                return "error", "validation_error", body
            return "error", "http_error", body
        # objeto cru (GET /users/me) -> embrulha como envelope completo
        return "complete", None, body

    def _write_trace(self, rec: dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _request(
        self,
        method: str,
        tool: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + path
        t0 = time.perf_counter()
        args: dict[str, Any] = {"method": method, "path": path}
        if params:
            args["params"] = params
        if json_body is not None:
            args["body"] = json_body

        try:
            resp = self._http.request(
                method, url, params=params, json=json_body, headers=self._headers()
            )
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            try:
                body: Any = resp.json()
            except Exception:
                body = {"_raw_text": resp.text}
            mode, notes, data = self._normalize(resp.status_code, body)
            http_status = resp.status_code
            error = None
        except Exception as exc:  # rede / conexao
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            body, data, notes = None, None, repr(exc)
            mode, http_status, error = "error", None, repr(exc)

        self._ordinal += 1
        rec = {
            "episode_id": self.ctx["episode_id"],
            "case_id": self.ctx["case_id"],
            "condicao": self.ctx["condicao"],
            "trial": self.ctx["trial"],
            "ordinal": self._ordinal,
            "ts": _now_iso(),
            "ferramenta": tool,
            "argumentos": args,
            "http_status": http_status,
            "mode": mode,
            "notes": notes,
            "payload": body,
            "latencia_ms": latency_ms,
        }
        if error:
            rec["error"] = error
        self._write_trace(rec)

        result = {
            "http_status": http_status,
            "mode": mode,
            "notes": notes,
            "data": data,
            "raw": body,
        }
        self.calls.append({"ferramenta": tool, "argumentos": args, **result})
        if isinstance(data, dict):
            self.received.append(data)
        return result

    # ------------------------------------------------------------------ verbs
    def get(self, tool: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = dict(params or {})
        if self.seed is not None and "seed" not in params:
            params["seed"] = self.seed
        return self._request("GET", tool, path, params=params)

    def get_me(self, tool: str = "get_me") -> dict[str, Any]:
        # /users/me nao aceita seed e nao usa envelope; _normalize embrulha.
        return self._request("GET", tool, "/users/me")

    def post(self, tool: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", tool, path, json_body=body)

    def patch(self, tool: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("PATCH", tool, path, json_body=body)

    def close(self) -> None:
        self._http.close()
