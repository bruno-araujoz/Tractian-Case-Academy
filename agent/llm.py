"""Isola o provedor LLM numa unica interface.

Um unico provedor, um unico modelo, uma unica temperatura (lidos do .env):
  LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE, GROQ_API_KEY
Tudo o mais do agente fala so com LLM.complete(...) — trocar de provedor
(Groq / OpenAI / qualquer endpoint OpenAI-compativel) e mudar so este arquivo.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys
import time
from typing import Any

from openai import APIError, BadRequestError, OpenAI, RateLimitError


def _parse_dur(s: str | None) -> float:
    """'25.74s' / '1m26.4s' / '577ms' -> segundos."""
    if not s:
        return 0.0
    if s.endswith("ms"):
        try:
            return float(s[:-2]) / 1000.0
        except ValueError:
            return 0.0
    total = 0.0
    for val, unit in re.findall(r"([\d.]+)\s*(ms|m|s)", s):
        f = float(val)
        total += f / 1000 if unit == "ms" else f * 60 if unit == "m" else f
    return total

_REPO = pathlib.Path(__file__).resolve().parent.parent


def load_env(path: str | pathlib.Path | None = None) -> None:
    path = pathlib.Path(path or (_REPO / ".env"))
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


class LLM:
    def __init__(self) -> None:
        load_env()
        self.provider = os.environ.get("LLM_PROVIDER", "groq")
        self.model = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
        self.temperature = float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        self.base_url = os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1")
        raw = (
            os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY", "")
            if self.provider == "groq"
            else os.environ.get("LLM_API_KEYS") or os.environ.get("LLM_API_KEY", "")
        )
        self._keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not self._keys:
            raise RuntimeError(f"Sem chave para o provedor '{self.provider}'. Configure o .env.")
        self._dead_keys: set[int] = set()
        # 1 client por chave; round-robin entre as vivas -> soma o TPM das chaves
        self._clients = [OpenAI(base_url=self.base_url, api_key=k, timeout=32, max_retries=1)
                         for k in self._keys]
        self._rr = 0
        self._key_idx = 0
        self.client = self._clients[0]
        # Gemini (via shim OpenAI) liga "thinking" por padrao -> respostas lentas e
        # estouro de max_tokens. Desliga.
        self._extra_kwargs: dict[str, Any] = {}
        if self.provider == "gemini" and "2.5-flash" in self.model and "lite" not in self.model:
            # desliga o "thinking" do gemini-2.5-flash (flash-lite nao tem thinking
            # e rejeita o parametro com 400)
            self._extra_kwargs = {"reasoning_effort": "none"}
        elif "gpt-oss" in self.model:
            # gpt-oss faz chain-of-thought por padrao (conta como completion tokens
            # e infla o TPM). "low" reduz sem perder a capacidade de tool-calling.
            self._extra_kwargs = {"reasoning_effort": "low"}
        # pacing contra o TPM (tokens/min) do free tier
        self._tpm_remaining: float | None = None
        self._tpm_reset_s: float = 0.0
        self._min_headroom = int(os.environ.get("LLM_TPM_HEADROOM", "2500"))
        # pacing contra o RPM (requests/min): intervalo minimo entre chamadas
        self._min_interval = float(os.environ.get("LLM_MIN_INTERVAL", "0"))
        self._last_call_ts = 0.0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.n_requests = 0

    def _live_keys(self) -> list[int]:
        return [i for i in range(len(self._keys)) if i not in self._dead_keys]

    def _pick_client(self) -> None:
        """Round-robin entre as chaves vivas (soma o TPM das chaves)."""
        live = self._live_keys()
        if not live:
            return
        self._key_idx = live[self._rr % len(live)]
        self._rr += 1
        self.client = self._clients[self._key_idx]

    def _rotate_key(self) -> bool:
        """Marca a chave atual como esgotada (cota diaria). False se acabaram todas."""
        self._dead_keys.add(self._key_idx)
        if not self._live_keys():
            return False
        self._pick_client()
        return True

    def config(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "n_chaves": len(self._keys),
        }

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 1200,
    ):
        kwargs: dict[str, Any] = dict(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=max_tokens,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        kwargs.update(self._extra_kwargs)

        # round-robin de chave a cada request: soma o TPM das N chaves
        self._pick_client()

        # pacing contra o RPM
        if self._min_interval:
            gap = time.time() - self._last_call_ts
            if gap < self._min_interval:
                time.sleep(self._min_interval - gap)

        # pacing proativo contra o TPM: so espera se TODAS as chaves estao no limite
        if self._tpm_remaining is not None and self._tpm_remaining < self._min_headroom \
                and len(self._live_keys()) <= 1:
            nap = min(self._tpm_reset_s + 1.0, 65.0)
            print(f"[llm] TPM baixo ({self._tpm_remaining:.0f}); dormindo {nap:.0f}s", file=sys.stderr, flush=True)
            time.sleep(nap)
            self._tpm_remaining = None

        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                raw = self.client.chat.completions.with_raw_response.create(**kwargs)
                self._read_limits(raw.headers)
                self._last_call_ts = time.time()
                resp = raw.parse()
                if getattr(resp, "usage", None):
                    self.total_prompt_tokens += resp.usage.prompt_tokens or 0
                    self.total_completion_tokens += resp.usage.completion_tokens or 0
                self.n_requests += 1
                return resp
            except RateLimitError as exc:
                last_exc = exc
                # NAO marca chave como morta (429 diario da Groq costuma ser janela
                # curta e volta). Estrategia unica: troca de chave + espera curta,
                # crescente. So o retry-after grande e respeitado ate um teto.
                self._pick_client()
                hdr = getattr(exc, "response", None)
                ra = _parse_dur(hdr.headers.get("retry-after")) if hdr is not None else 0.0
                wait = min(ra, 20.0) if ra else min(2 * (attempt + 1), 15.0)
                print(f"[llm] 429 (tent {attempt+1}/6); troca de chave + {wait:.0f}s",
                      file=sys.stderr, flush=True)
                time.sleep(wait)
                self._tpm_remaining = None
            except BadRequestError as exc:
                last_exc = exc
                print(f"[llm] 400 {str(exc)[:120]}; retry {attempt+1}/2", file=sys.stderr, flush=True)
                if attempt >= 1:
                    break
                time.sleep(2)
            except APIError as exc:
                last_exc = exc
                print(f"[llm] APIError {type(exc).__name__}; troca chave, retry {attempt+1}/3", file=sys.stderr, flush=True)
                self._pick_client()
                if attempt >= 2:
                    break
                time.sleep(2)
        raise RuntimeError(f"LLM falhou apos retries: {last_exc!r}")

    def _read_limits(self, headers: Any) -> None:
        try:
            rem = headers.get("x-ratelimit-remaining-tokens")
            rst = headers.get("x-ratelimit-reset-tokens")
            if rem is not None:
                self._tpm_remaining = float(rem)
            if rst is not None:
                self._tpm_reset_s = _parse_dur(rst)
        except Exception:
            pass
