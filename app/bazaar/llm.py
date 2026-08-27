"""LLM provider adapter — one async `complete()` over mock | gemini | groq.

Zero new dependencies: raw REST over httpx (already required by rzp.py).
  gemini : generativelanguage.googleapis.com v1beta generateContent
  groq   : OpenAI-compatible chat/completions
  nvidia : NIM integrate.api.nvidia.com, OpenAI-compatible — break-glass backup
  mock   : deterministic, keyless — dev/CI and no-key fallback

Free-tier etiquette: single retry after a short sleep on 429/5xx, then fail.
Callers own their prompts; this module never parses agent JSON.
"""

import asyncio

import httpx

from bazaar.config import settings

_TIMEOUT = httpx.Timeout(90.0, connect=10.0)  # gemini 3.x can think >45 s
_NVIDIA_TIMEOUT = httpx.Timeout(150.0, connect=15.0)  # free-tier queue spikes
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
_GROQ_BASE = "https://api.groq.com/openai/v1"
_GROQ_MODEL = "llama-3.3-70b-versatile"
_NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class LLMError(RuntimeError):
    pass


def _resolve_model(explicit: str | None, smart: bool) -> tuple[str, str]:
    """Returns (provider, model)."""
    provider = settings.llm_provider
    if provider == "gemini":
        if not settings.gemini_api_key:
            raise LLMError("LLM_PROVIDER=gemini but GEMINI_API_KEY is unset")
        return provider, explicit or (
            settings.llm_model_smart if smart else settings.llm_model)
    if provider == "groq":
        if not settings.groq_api_key:
            raise LLMError("LLM_PROVIDER=groq but GROQ_API_KEY is unset")
        return provider, explicit or _GROQ_MODEL
    if provider == "nvidia":
        if not settings.nvidia_api_key:
            raise LLMError("LLM_PROVIDER=nvidia but NVIDIA_API_KEY is unset")
        return provider, explicit or settings.nvidia_model
    if provider == "openrouter":
        if not settings.openrouter_api_key:
            raise LLMError(
                "LLM_PROVIDER=openrouter but OPENROUTER_API_KEY is unset")
        return provider, explicit or settings.openrouter_model
    return "mock", explicit or "mock-1"


async def _gemini_call(model: str, system: str, messages: list[dict],
                       json_mode: bool, temperature: float,
                       max_tokens: int) -> str:
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    gen_cfg: dict = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    url = f"{_GEMINI_BASE}/models/{model}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code == 401:  # AQ.-format keys may want a bearer
            resp = await client.post(
                url, json=body,
                headers={"Authorization": f"Bearer {settings.gemini_api_key}"},
            )
        if resp.status_code >= 400:
            raise LLMError(f"gemini {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"gemini odd response: {str(data)[:300]}") from exc


async def _groq_call(model: str, system: str, messages: list[dict],
                     json_mode: bool, temperature: float,
                     max_tokens: int) -> str:
    msgs = [{"role": "system", "content": system}] + messages
    body: dict = {"model": model, "messages": msgs,
                  "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{_GROQ_BASE}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        )
        if resp.status_code >= 400:
            raise LLMError(f"groq {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]


async def _nvidia_call(model: str, system: str, messages: list[dict],
                       json_mode: bool, temperature: float,
                       max_tokens: int) -> str:
    return await _openai_compat(
        _NVIDIA_BASE, settings.nvidia_api_key, model, system, messages,
        json_mode, temperature, max_tokens, _NVIDIA_TIMEOUT, "nvidia")


async def _openrouter_call(model: str, system: str, messages: list[dict],
                           json_mode: bool, temperature: float,
                           max_tokens: int) -> str:
    # OpenRouter is OpenAI-compatible too; free-tier lanes throttle with a
    # rolling window that recovers in seconds — the shared retry loop above
    # absorbs it (429 counts as transient).
    return await _openai_compat(
        _OPENROUTER_BASE, settings.openrouter_api_key, model, system,
        messages, json_mode, temperature, max_tokens, _TIMEOUT, "openrouter")


async def _openai_compat(base: str, api_key: str, model: str, system: str,
                         messages: list[dict], json_mode: bool,
                         temperature: float, max_tokens: int,
                         timeout: httpx.Timeout, label: str) -> str:
    """One OpenAI-compatible chat/completions call (groq/nvidia/openrouter)."""
    msgs = [{"role": "system", "content": system}] + messages
    body: dict = {"model": model, "messages": msgs,
                  "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            f"{base}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code >= 400:
            raise LLMError(f"{label} {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]


async def _mock_call(system: str, messages: list[dict]) -> str:
    """Deterministic stand-in so agents are developable/testable keylessly."""
    last = messages[-1]["content"] if messages else ""
    return (
        '{"mock": true, "reply": "mock response", '
        '"echo_last_user": ' + __import__("json").dumps(last[:200]) + ", "
        '"system_seen": ' + __import__("json").dumps(system[:120]) + "}"
    )


async def complete(system: str, messages: list[dict], *,
                   model: str | None = None,
                   smart: bool = False,
                   json_mode: bool = False,
                   temperature: float = 0.7,
                   max_tokens: int = 800,
                   retries: int = 2) -> str:  # free-tier queues spike hard;
    # 3 attempts × 150 s window absorbs double-stalls (NIM measured live)
    """One completion. `smart=True` routes to the slow-lane model (daily
    strategy calls); default lane is for high-volume per-session work."""
    provider, resolved = _resolve_model(model, smart)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            if provider == "gemini":
                return await _gemini_call(resolved, system, messages,
                                          json_mode, temperature, max_tokens)
            if provider == "groq":
                return await _groq_call(resolved, system, messages,
                                        json_mode, temperature, max_tokens)
            if provider == "nvidia":
                return await _nvidia_call(resolved, system, messages,
                                          json_mode, temperature, max_tokens)
            if provider == "openrouter":
                return await _openrouter_call(resolved, system, messages,
                                              json_mode, temperature,
                                              max_tokens)
            return await _mock_call(system, messages)
        except LLMError as exc:
            last_err = exc
            transient = " 429" in str(exc) or " 5" in str(exc)[:12]
            if attempt < retries and transient:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
            raise
        except httpx.HTTPError as exc:
            # transport-level (timeout/reset) — always retryable, and the
            # adapter contract says callers only ever see LLMError (a raw
            # ReadTimeout here killed a measurement run once)
            last_err = LLMError(f"{provider} transport: "
                                f"{type(exc).__name__}: {str(exc)[:160]}")
            if attempt < retries:
                await asyncio.sleep(min(2.0 * (attempt + 1), 10.0))
                continue
            raise last_err
    raise last_err  # pragma: no cover
