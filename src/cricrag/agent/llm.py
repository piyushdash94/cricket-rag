"""Groq chat client (OpenAI-compatible API) with model fallback and an on-disk response cache.

The cache key is the model list plus the exact request, so reruns of an eval spend no tokens and give
the same answer. Rate limits: wait briefly when Groq says so, otherwise move to the next model.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    pass


class GroqClient:
    def __init__(
        self,
        api_key: str | None,
        models: list[str],
        cache_dir: Path | None = None,
        client: httpx.Client | None = None,
        max_wait: float = 20.0,
    ):
        self.api_key, self.models = api_key, models
        self.cache_dir = cache_dir
        self.http = client or httpx.Client(timeout=60)
        self.max_wait = max_wait
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "cache_hits": 0}

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.models)

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict] | None = None, max_tokens: int = 900
    ) -> dict[str, Any]:
        """Returns {"message": {...}, "model": str}. Raises LLMUnavailable when every model fails."""
        if not self.available:
            raise LLMUnavailable("no GROQ_API_KEY")
        key = self._key(messages, tools, max_tokens)
        if (hit := self._cache_get(key)) is not None:
            self.usage["cache_hits"] += 1
            return hit
        errors = []
        for model in self.models:
            body: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "temperature": 0,
                "max_tokens": max_tokens,
            }
            if tools:
                body |= {"tools": tools, "tool_choice": "auto"}
            if model.startswith("openai/gpt-oss"):
                body["reasoning_effort"] = "low"
            for _attempt in range(2):
                try:
                    r = self.http.post(
                        GROQ_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"}
                    )
                except httpx.HTTPError as exc:
                    errors.append(f"{model}: {exc!r}")
                    break
                if r.status_code == 429:
                    wait = _retry_after(r)
                    if wait is not None and wait <= self.max_wait:
                        time.sleep(wait + 0.5)
                        continue
                    errors.append(f"{model}: rate limited")
                    break
                if r.status_code >= 400:
                    errors.append(f"{model}: HTTP {r.status_code} {r.text[:160]}")
                    break
                data = r.json()
                u = data.get("usage") or {}
                self.usage["prompt_tokens"] += u.get("prompt_tokens", 0)
                self.usage["completion_tokens"] += u.get("completion_tokens", 0)
                self.usage["calls"] += 1
                out = {"message": data["choices"][0]["message"], "model": model}
                self._cache_put(key, out)
                return out
        raise LLMUnavailable("; ".join(errors) or "no model answered")

    def evict(
        self, messages: list[dict[str, Any]], tools: list[dict] | None = None, max_tokens: int = 900
    ) -> None:
        """Drop a cached reply (used when it failed validation, so a rerun asks again)."""
        if self.cache_dir:
            (self.cache_dir / f"{self._key(messages, tools, max_tokens)}.json").unlink(missing_ok=True)

    def _key(self, messages, tools, max_tokens) -> str:
        blob = json.dumps({"m": self.models, "msg": messages, "t": tools, "n": max_tokens}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _cache_get(self, key: str) -> dict | None:
        if not self.cache_dir:
            return None
        p = self.cache_dir / f"{key}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None

    def _cache_put(self, key: str, value: dict) -> None:
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            (self.cache_dir / f"{key}.json").write_text(json.dumps(value), encoding="utf-8")


def _retry_after(r: httpx.Response) -> float | None:
    if r.headers.get("retry-after"):
        try:
            return float(r.headers["retry-after"])
        except ValueError:
            pass
    m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", r.text)
    return (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else None
