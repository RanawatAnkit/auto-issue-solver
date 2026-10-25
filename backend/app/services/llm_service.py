import json
import re
import time
import logging
from contextlib import contextmanager
from contextvars import ContextVar
import httpx
import openai
from pydantic import BaseModel, ValidationError
from typing import Type, TypeVar, Optional
from app.config import settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

# Rough characters-per-token ratio for code-heavy prompts; used only for budgeting.
CHARS_PER_TOKEN = 3

# ---------------------------------------------------------------------------
# Settings helpers (tests replace `settings` with a MagicMock, whose missing
# attributes are MagicMocks rather than defaults)
# ---------------------------------------------------------------------------

def _setting(name: str, default, kind: type):
    value = getattr(settings, name, default)
    if kind is int and isinstance(value, bool):
        return default
    return value if isinstance(value, kind) else default

# ---------------------------------------------------------------------------
# Usage metering
# ---------------------------------------------------------------------------

_usage_records: ContextVar[Optional[list]] = ContextVar("llm_usage_records", default=None)


@contextmanager
def track_llm_usage():
    """Collect one record per LLM attempt made inside the block (e.g. one agent run)."""
    records: list = []
    token = _usage_records.set(records)
    try:
        yield records
    finally:
        _usage_records.reset(token)


def _record_usage(**record):
    records = _usage_records.get()
    if records is not None:
        records.append(record)


def summarize_llm_usage(records: list) -> dict:
    """Totals per run. Token counts are only those the provider reported; cost is not estimated."""
    summary = {
        "calls": len(records),
        "failed_calls": sum(1 for r in records if not r.get("success")),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in records),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in records),
        "calls_without_token_counts": sum(1 for r in records if r.get("prompt_tokens") is None),
        "duration_seconds": round(sum(r.get("duration_seconds") or 0 for r in records), 2),
        "by_schema": {},
        "models": sorted({f"{r.get('provider')}/{r.get('model')}" for r in records}),
    }
    summary["total_tokens"] = summary["prompt_tokens"] + summary["completion_tokens"]
    for r in records:
        s = summary["by_schema"].setdefault(r.get("schema"), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        s["calls"] += 1
        s["prompt_tokens"] += r.get("prompt_tokens") or 0
        s["completion_tokens"] += r.get("completion_tokens") or 0
    return summary

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

class LLMOutputError(ValueError):
    """The model answered, but the answer is not valid for the requested schema."""
    def __init__(self, message: str, raw_output: str = ""):
        super().__init__(message)
        self.raw_output = raw_output

class LLMUnavailableError(RuntimeError):
    """The provider could not be reached or did not answer in time."""

class LLMTimeoutError(LLMUnavailableError, TimeoutError):
    pass

class LLMRefusalError(RuntimeError):
    """The model declined to answer; the same request would be refused again."""

class LLMRequestError(RuntimeError):
    """The provider rejected the request (bad model name, auth, oversized prompt); retrying cannot help."""

_QUOTA_KEYWORDS = ("quota", "rate limit", "rate_limit", "resource_exhausted", "resourceexhausted", "too many requests")

def _is_quota_error(exc: Exception) -> bool:
    """Return True only for quota-exhaustion / rate-limit errors."""
    if isinstance(exc, openai.RateLimitError):
        return True

    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)

    if status == 429:
        return True

    msg_lower = str(exc).lower()

    # HTTP 403 is quota only when the body says so (Gemini RESOURCE_EXHAUSTED)
    if status == 403:
        return any(kw in msg_lower for kw in _QUOTA_KEYWORDS)

    # Catch provider SDKs that surface quota info in the message
    if any(kw in msg_lower for kw in _QUOTA_KEYWORDS):
        if status in (401, 403):
            return False
        return True

    return False

def _is_transient_error(exc: Exception) -> bool:
    """Return True for transient server errors worth retrying."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (500, 502, 503, 504):
        return True
    if isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError, LLMUnavailableError)):
        return True
    return False

def _is_non_retryable(exc: Exception) -> bool:
    if isinstance(exc, (LLMRequestError, LLMRefusalError)):
        return True
    return isinstance(exc, (
        openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError, openai.BadRequestError,
    )) and not _is_quota_error(exc)

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?key|authorization|bearer)[\"'=:\s]+[A-Za-z0-9_\-\.]{8,}"), r"\1=***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}"), "sk-***"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "AIza***"),
    (re.compile(r"key=[A-Za-z0-9_\-]{10,}"), "key=***"),
]

def _sanitize(message: str, limit: int = 600) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        message = pattern.sub(repl, message)
    return message if len(message) <= limit else message[:limit] + "...(truncated)"

# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?(.*?)\n?```$", re.DOTALL)

def _extract_json_text(raw: str) -> str:
    """Strip markdown fences and surrounding prose around a JSON object."""
    text = raw.strip()
    m = _FENCE_RE.match(text)
    if m:
        text = m.group(1).strip()
    if text.startswith("{"):
        return text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return text

def parse_structured_output(raw: str, response_model: Type[T], schema: dict | None = None) -> T:
    """Validate raw model text against response_model, raising LLMOutputError with a precise reason."""
    if not raw or not raw.strip():
        raise LLMOutputError("Model returned empty response content.", raw)
    text = _extract_json_text(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMOutputError(f"Response is not valid JSON ({e.msg} at position {e.pos}).", raw) from e
    if not isinstance(data, dict):
        raise LLMOutputError("Response must be a JSON object.", raw)

    schema = schema or response_model.model_json_schema()
    required = schema.get("required", [])
    if "properties" in data and "type" in data and any(f not in data for f in required):
        raise LLMOutputError(
            "Model returned the JSON schema definition instead of populated data. "
            f"Missing required fields: {[f for f in required if f not in data]}",
            raw,
        )
    try:
        return response_model.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()[:10]
        )
        raise LLMOutputError(f"Response failed Pydantic validation: {problems}", raw) from exc

# ---------------------------------------------------------------------------
# Ollama availability check (called once, not per inference)
# ---------------------------------------------------------------------------

def verify_ollama_available(base_url: str = None, model: str = None) -> dict:
    """Check whether Ollama is running and the specified model is available.

    Returns:
        dict: {"available": bool, "message": str, "command": Optional[str]}
    """
    raw_base = base_url or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
    base = str(raw_base).rstrip("/")
    mdl = model or getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b")

    try:
        resp = httpx.get(f"{base}/api/tags", timeout=5)
        resp.raise_for_status()
        models_list = resp.json().get("models", [])
        available_names = [m.get("name", "") for m in models_list]

        # Exact match or prefix match (e.g. qwen2.5-coder:7b vs qwen2.5-coder:7b-instruct)
        matched = False
        target_base = mdl.split(":")[0]
        for name in available_names:
            if name == mdl or name == f"{mdl}:latest":
                matched = True
                break
            # Match base if tag specified
            if ":" in mdl and name.startswith(mdl):
                matched = True
                break
            if name.split(":")[0] == target_base and ":" not in mdl:
                matched = True
                break

        if matched:
            return {
                "available": True,
                "message": f"Ollama is reachable and model '{mdl}' is available.",
                "command": None,
            }

        return {
            "available": False,
            "message": (
                f"Ollama is running at {base}, but model '{mdl}' was not found. "
                f"Available models: {available_names}. "
                f"Run: ollama pull {mdl}"
            ),
            "command": f"ollama pull {mdl}",
        }
    except httpx.ConnectError:
        return {
            "available": False,
            "message": (
                f"Cannot connect to Ollama at {base}. Is Ollama running? "
                "Start it with: ollama serve"
            ),
            "command": "ollama serve",
        }
    except Exception as e:
        return {
            "available": False,
            "message": f"Ollama health check failed: {e}",
            "command": f"ollama pull {mdl}",
        }

# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------

# OpenRouter counts as paid: whether a model is free depends on the model chosen, and a
# silent fallback must never be able to start charging.
PAID_PROVIDERS = {"openai", "gemini", "openrouter"}

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# Native tool-call syntaxes (e.g. Poolside '<tool_call>name<arg_key>..', Hermes '<tool_call>{..}', Llama '<function=..>').
_TOOL_CALL_RE = re.compile(r"<tool_call>|<\|tool_call|<function=|<arg_key>", re.IGNORECASE)


# Gemini's OpenAI-compatible endpoint rejects the planner schema with a bare "400 Request contains an
# invalid argument" (run 326). Measured: removing pattern or min/maxLength did not help; removing
# minItems/maxItems did. A single flat array with them was accepted, so the limit appears to concern
# nested schemas. They are left out of the schema sent to Gemini only; Pydantic still enforces them on
# the answer, and a violation is retried with the validation error as feedback.
_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset({"minItems", "maxItems"})


def _strip_schema_keys(node, keys):
    if isinstance(node, dict):
        return {k: _strip_schema_keys(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [_strip_schema_keys(v, keys) for v in node]
    return node


def _without_array_counts(model: Type[T]) -> Type[T]:
    """A subclass whose advertised JSON schema has no minItems/maxItems; validation is unchanged."""
    class GeminiSchema(model):  # type: ignore[valid-type, misc]
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            return _strip_schema_keys(super().model_json_schema(*args, **kwargs), _GEMINI_UNSUPPORTED_SCHEMA_KEYS)
    GeminiSchema.__name__ = GeminiSchema.__qualname__ = model.__name__
    return GeminiSchema


def _str_or_none(value) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
_NO_STRUCTURED_OUTPUT = ("response_format", "json_schema", "structured output", "structured_output")
_UNKNOWN_MODEL = ("not a valid model", "model not found", "no endpoints found", "is not available", "does not exist")


def _reasoning_tokens(response) -> int | None:
    """Reasoning tokens reported by the provider, or None when it did not report them."""
    details = getattr(getattr(response, "usage", None), "completion_tokens_details", None)
    value = getattr(details, "reasoning_tokens", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else None

class LLMService:
    def __init__(self):
        raw_provider = getattr(settings, "LLM_PROVIDER", "ollama")
        self.provider = str(raw_provider).lower() if isinstance(raw_provider, str) else "ollama"

        raw_fallback = getattr(settings, "LLM_FALLBACK_PROVIDER", None)
        if isinstance(raw_fallback, str) and raw_fallback.strip():
            self.fallback_provider = raw_fallback.strip().lower()
        else:
            self.fallback_provider = None

        self.max_retries = _setting("MAX_LLM_RETRIES", 2, int)
        self.allow_paid_fallback = _setting("LLM_ALLOW_PAID_FALLBACK", False, bool)
        self.fallback_on = _setting("LLM_FALLBACK_ON", "quota", str).lower()

        # Build primary config
        self.primary = self._build_provider_config(self.provider)

        # Build fallback config (if set and different from primary)
        self.fallback = None
        if self.fallback_provider and self.fallback_provider != self.provider:
            self.fallback = self._build_provider_config(self.fallback_provider)

        # OpenRouter adaptations learned during this service's lifetime (see _call_openrouter).
        self._reasoning_allowance = _setting("OPENROUTER_REASONING_TOKENS", 8192, int)
        self._openrouter_format = "json_schema"

        # Backward compatibility: expose flat attributes used by existing tests
        self.api_key = self.primary["api_key"]
        self.model = self.primary["model"]
        self.base_url = self.primary["base_url"]

        logger.info(
            "LLMService initialized [Provider: %s, Model: %s, Fallback: %s]",
            self.provider,
            self.model,
            self.fallback_provider,
        )

    # ---- provider config builder ----

    @staticmethod
    def _build_provider_config(provider_name: str) -> dict:
        if provider_name == "ollama":
            raw_base = getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
            base = str(raw_base).rstrip("/")
            model_name = getattr(settings, "OLLAMA_MODEL", "qwen2.5-coder:7b")
            return {
                "name": "ollama",
                "api_key": "ollama",  # dummy key for compatibility
                "model": model_name,
                "base_url": f"{base}/v1",
                "ollama_base": base,
                "is_ollama": True,
            }
        elif provider_name == "openai":
            return {
                "name": "openai",
                "api_key": settings.OPENAI_API_KEY,
                "model": getattr(settings, "OPENAI_MODEL", "gpt-4o"),
                "base_url": None,
                "is_ollama": False,
            }
        elif provider_name == "gemini":
            return {
                "name": "gemini",
                "api_key": settings.GEMINI_API_KEY,
                "model": getattr(settings, "GEMINI_MODEL", "gemini-1.5-pro"),
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                "is_ollama": False,
            }
        elif provider_name == "openrouter":
            return {
                "name": "openrouter",
                "api_key": settings.OPENROUTER_API_KEY,
                "model": getattr(settings, "OPENROUTER_MODEL", None),
                "base_url": _setting("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1", str),
                "timeout": _setting("OPENROUTER_TIMEOUT", 300, int),
                "is_ollama": False,
            }
        else:
            raise NotImplementedError(
                f"LLM Provider '{provider_name}' not supported. "
                "Set LLM_PROVIDER to one of: ollama, gemini, openai, openrouter."
            )

    # ---- prompt budgeting ----

    def prompt_budget_chars(self) -> int:
        """Approximate number of prompt characters the primary provider can accept.

        Agents use this to trim repository context *before* calling the model,
        instead of letting the provider silently cut off the start of the prompt.
        """
        if self.primary["is_ollama"]:
            num_ctx = _setting("OLLAMA_NUM_CTX", 16384, int)
            num_predict = _setting("OLLAMA_NUM_PREDICT", 4096, int)
            return max((num_ctx - num_predict - 512) * CHARS_PER_TOKEN, 4000)
        if self.primary["name"] == "openrouter":
            # Reasoning tokens share the output budget, so they are reserved as well.
            context = _setting("OPENROUTER_CONTEXT_TOKENS", 32000, int)
            reserved = _setting("OPENROUTER_MAX_TOKENS", 4096, int) + self._reasoning_allowance
            return max((context - reserved - 512) * CHARS_PER_TOKEN, 8000)
        return max(_setting("MAX_CONTEXT_TOKENS", 12000, int) * CHARS_PER_TOKEN, 8000)

    # ---- single-provider call ----

    def _call_provider(self, cfg: dict, system_prompt: str, user_prompt: str, response_model: Type[T],
                       opts: dict | None = None) -> T:
        """Call one provider with bounded retries.

        - invalid output: retried with the validation error fed back to the model
        - transient/server errors: retried with backoff (timeouts at most once)
        - quota errors: raised immediately so the caller can decide on fallback
        - configuration/request errors: raised immediately, retrying cannot help
        """
        # API key validation (skip for Ollama)
        if not cfg["is_ollama"] and not cfg["api_key"]:
            raise ValueError(
                f"Configuration Error: {cfg['name'].upper()}_API_KEY not configured. "
                "Cannot process real LLM requests."
            )

        if cfg["name"] == "openrouter" and not cfg.get("model"):
            raise ValueError("Configuration Error: OPENROUTER_MODEL not configured (e.g. 'qwen/qwen-2.5-coder-32b-instruct').")

        client = None
        if not cfg["is_ollama"]:
            if cfg.get("timeout"):
                client = openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=cfg["timeout"])
            elif cfg.get("base_url"):
                client = openai.OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
            else:
                client = openai.OpenAI(api_key=cfg["api_key"])

        feedback: list[dict] = []
        timeouts = 0
        last_error: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            self._last_usage = None
            try:
                if cfg["is_ollama"]:
                    result = self._call_ollama(client, cfg, system_prompt, user_prompt, response_model, feedback)
                elif cfg["name"] == "openrouter":
                    result = self._call_openrouter(client, cfg, system_prompt, user_prompt, response_model,
                                                   feedback, opts or {})
                else:
                    result = self._call_cloud(client, cfg, system_prompt, user_prompt, response_model, feedback)
                logger.info(
                    "LLM call ok [provider=%s model=%s schema=%s attempt=%d/%d duration=%.1fs]",
                    cfg["name"], cfg["model"], response_model.__name__, attempt, attempts, time.monotonic() - started,
                )
                self._meter(cfg, response_model, started, success=True)
                return result
            except Exception as e:
                last_error = e
                self._meter(cfg, response_model, started, success=False, error=type(e).__name__)
                logger.warning(
                    "LLM call failed [provider=%s model=%s schema=%s attempt=%d/%d duration=%.1fs]: %s: %s",
                    cfg["name"], cfg["model"], response_model.__name__, attempt, attempts,
                    time.monotonic() - started, type(e).__name__, _sanitize(str(e)),
                )
                if _is_quota_error(e) or _is_non_retryable(e):
                    raise
                if isinstance(e, (LLMTimeoutError, openai.APITimeoutError)):
                    timeouts += 1
                    if timeouts > 1:
                        break
                if attempt == attempts:
                    break
                if isinstance(e, LLMOutputError) and not (e.raw_output or "").strip():
                    # Never quote an empty answer back: some providers (Cohere, runs 311-312)
                    # reject an empty assistant message with HTTP 400.
                    # No raw text is available (empty answer, or rejected inside the SDK): state the error only.
                    feedback = [{"role": "user", "content": (
                        f"Your previous response was rejected: {e}\nReturn ONLY a JSON object that "
                        "satisfies the schema, with every required field populated."
                    )}]
                elif isinstance(e, LLMOutputError):
                    # Same prompt at temperature 0 would produce the same answer;
                    # show the model what was wrong instead.
                    feedback = [
                        {"role": "assistant", "content": (e.raw_output or "")[:4000]},
                        {"role": "user", "content": (
                            f"Your previous response was rejected: {e}\n"
                            "Return ONLY a corrected JSON object that satisfies the schema, with every required field populated."
                        )},
                    ]
                else:
                    time.sleep(min(2 ** (attempt - 1), 8))

        raise RuntimeError(
            f"[{cfg['name']}] maximum retry bounds exceeded after {attempt} attempt(s): {_sanitize(str(last_error))}"
        ) from last_error

    def _meter(self, cfg: dict, response_model, started: float, success: bool, error: str | None = None):
        usage = getattr(self, "_last_usage", None) or {}
        _record_usage(
            provider=cfg["name"], model=cfg["model"], schema=response_model.__name__, success=success, error=error,
            prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
            duration_seconds=round(time.monotonic() - started, 3),
        )

    def _call_openrouter(self, client, cfg: dict, system_prompt: str, user_prompt: str, response_model: Type[T],
                         feedback=None, opts: dict | None = None) -> T:
        """OpenRouter (OpenAI-compatible) call that copes with the variety of routed models.

        - JSON-schema structured output, downgraded once to json_object for models without it
          (remembered for this service); the schema is also in the system prompt either way
        - <think> blocks and code fences are stripped before parsing
        - reasoning tokens share max_tokens, so an allowance is added on top of the answer cap and
          doubled (up to OPENROUTER_REASONING_TOKENS_MAX) when reasoning alone used it up
        """
        opts = opts or {}
        schema = response_model.model_json_schema()
        answer_cap = opts.get("max_output_tokens") or _setting("OPENROUTER_MAX_TOKENS", 4096, int)
        ceiling = _setting("OPENROUTER_REASONING_TOKENS_MAX", 32768, int)
        temperature = opts.get("temperature")
        messages = [
            {"role": "system", "content": (
                f"{system_prompt}\n\nReturn ONLY a JSON object (no prose, no markdown) that conforms to this JSON "
                f"schema:\n{json.dumps(schema)}")},
            {"role": "user", "content": user_prompt},
            *(feedback or []),
        ]

        while True:
            if self._openrouter_format == "json_schema":
                response_format = {"type": "json_schema", "json_schema": {
                    "name": response_model.__name__, "schema": schema, "strict": False}}
            else:
                response_format = {"type": "json_object"}
            max_tokens = answer_cap + self._reasoning_allowance
            try:
                response = client.chat.completions.create(
                    model=cfg["model"], messages=messages, response_format=response_format,
                    max_tokens=max_tokens, temperature=0 if temperature is None else temperature,
                )
            except openai.BadRequestError as e:
                text = str(e).lower()
                if self._openrouter_format == "json_schema" and any(k in text for k in _NO_STRUCTURED_OUTPUT):
                    logger.warning("OpenRouter model %s does not accept json_schema; using json_object", cfg["model"])
                    self._openrouter_format = "json_object"
                    continue
                if any(k in text for k in _UNKNOWN_MODEL):
                    raise LLMRequestError(
                        f"OpenRouter does not serve model '{cfg['model']}'. Check OPENROUTER_MODEL against "
                        f"https://openrouter.ai/models. ({_sanitize(str(e), 200)})") from e
                raise LLMRequestError(f"OpenRouter rejected the request: {_sanitize(str(e), 300)}") from e
            except openai.NotFoundError as e:
                raise LLMRequestError(
                    f"OpenRouter does not serve model '{cfg['model']}'. Check OPENROUTER_MODEL. "
                    f"({_sanitize(str(e), 200)})") from e

            usage = getattr(response, "usage", None)
            if isinstance(getattr(usage, "prompt_tokens", None), int):
                self._last_usage = {"prompt_tokens": usage.prompt_tokens,
                                    "completion_tokens": getattr(usage, "completion_tokens", None)}
            choices = getattr(response, "choices", None)
            if not isinstance(choices, list) or not choices:
                # OpenRouter can answer HTTP 200 with an error object instead of choices (upstream failure).
                upstream = _str_or_none(getattr(response, "error", None)) or "no error details"
                raise LLMUnavailableError(f"OpenRouter returned no choices (provider error): {_sanitize(upstream, 300)}")
            choice = choices[0]
            content = getattr(choice.message, "content", None)
            raw = content if isinstance(content, str) else ""
            info = self._openrouter_response_info(response, choice, raw, max_tokens)
            logger.info("OpenRouter response %s", info)

            if choice.finish_reason == "length":
                reasoning = _reasoning_tokens(response)
                # Grow the allowance only on evidence that reasoning itself used up its share.
                if reasoning is not None and reasoning >= self._reasoning_allowance:
                    if self._reasoning_allowance >= ceiling:
                        raise LLMRequestError(
                            f"OpenRouter model '{cfg['model']}' spent {reasoning} tokens reasoning and was cut off "
                            f"even with the maximum allowance of {ceiling} (OPENROUTER_REASONING_TOKENS_MAX). Raise "
                            "that limit or choose a model that reasons less.")
                    self._reasoning_allowance = min(self._reasoning_allowance * 2, ceiling)
                    logger.warning("OpenRouter reasoning used %d tokens; retrying with a %d-token allowance",
                                   reasoning, self._reasoning_allowance)
                    continue
                if reasoning is not None:
                    raise LLMOutputError(
                        f"OpenRouter response was cut off at max_tokens={max_tokens}: the answer itself was too long "
                        f"(reasoning used {reasoning} of its {self._reasoning_allowance}-token allowance).", raw)
                raise LLMOutputError(
                    f"OpenRouter response was cut off at max_tokens={max_tokens} before the JSON was complete.", raw)
            break

        if info["refusal"]:
            raise LLMRefusalError(f"OpenRouter model '{cfg['model']}' refused: {_sanitize(info['refusal'], 300)}")
        if not raw.strip():
            if info["reasoning_chars"]:
                raise LLMOutputError(
                    f"OpenRouter response contained no text: the model produced {info['reasoning_chars']} characters "
                    "of reasoning but no final answer.", "")
            raise LLMOutputError("OpenRouter response contained no text.", "")
        answer = _THINK_RE.sub("", raw).strip()
        if _TOOL_CALL_RE.search(answer):
            # Agentic models may answer in their native tool-call syntax when the prompt describes actions.
            raise LLMOutputError(
                "OpenRouter response used tool-call markup instead of a JSON object. Tools cannot be called here: "
                "answer with the JSON object only, and put any requested actions in its fields (e.g. 'searches').",
                raw)
        try:
            return parse_structured_output(answer, response_model, schema)
        except LLMOutputError as exc:
            mode = ("structured output" if self._openrouter_format == "json_schema"
                    else "JSON mode, which this provider may not enforce")
            raise LLMOutputError(
                f"OpenRouter response failed validation ({mode}; finish_reason={info['finish_reason']}, "
                f"{info['content_chars']} chars): {exc}", raw) from exc

    @staticmethod
    def _openrouter_response_info(response, choice, raw: str, max_tokens: int) -> dict:
        """Structure of one response for diagnostics: no keys, and only a short sanitized preview of the answer."""
        message = choice.message
        reasoning = _str_or_none(getattr(message, "reasoning", None))
        usage = getattr(response, "usage", None)
        return {
            "model": _str_or_none(getattr(response, "model", None)),
            "provider": _str_or_none(getattr(response, "provider", None)),
            "finish_reason": _str_or_none(getattr(choice, "finish_reason", None)),
            "max_tokens": max_tokens,
            "prompt_tokens": _int_or_none(getattr(usage, "prompt_tokens", None)),
            "completion_tokens": _int_or_none(getattr(usage, "completion_tokens", None)),
            "total_tokens": _int_or_none(getattr(usage, "total_tokens", None)),
            "reasoning_tokens": _reasoning_tokens(response),
            "content_type": type(getattr(message, "content", None)).__name__,
            "content_chars": len(raw),
            "preview": _sanitize(" ".join(raw.split())[:120], 160),
            "reasoning_chars": len(reasoning) if reasoning else 0,
            "refusal": _str_or_none(getattr(message, "refusal", None)),
        }

    def _call_cloud(self, client, cfg: dict, system_prompt: str, user_prompt: str, response_model: Type[T], feedback=None) -> T:
        """Cloud provider call using beta structured output parsing."""
        format_model = _without_array_counts(response_model) if cfg["name"] == "gemini" else response_model
        try:
            response = client.beta.chat.completions.parse(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    *(feedback or []),
                ],
                response_format=format_model,
            )
        except (ValidationError, openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as e:
            raise LLMOutputError(f"Structured output rejected: {e}") from e
        usage = getattr(response, "usage", None)
        if isinstance(getattr(usage, "prompt_tokens", None), int):
            self._last_usage = {"prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": getattr(usage, "completion_tokens", None)}
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise LLMOutputError("Parsed structured output returned Null.")
        if format_model is not response_model and isinstance(parsed, format_model):
            # Hand callers the class they asked for, not the schema-only subclass.
            parsed = response_model.model_validate(parsed.model_dump())
        return parsed

    def _call_ollama(
        self,
        client,
        cfg: dict,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        feedback=None,
    ) -> T:
        """Call Ollama with native schema-constrained structured output via /api/chat."""
        schema = response_model.model_json_schema()
        raw_base = cfg.get("ollama_base") or getattr(settings, "OLLAMA_BASE_URL", "http://localhost:11434")
        base_url = str(raw_base).rstrip("/")
        timeout = _setting("OLLAMA_TIMEOUT", 600, int)
        num_ctx = _setting("OLLAMA_NUM_CTX", 16384, int)
        num_predict = _setting("OLLAMA_NUM_PREDICT", 4096, int)

        # Clear instructions to populate fields rather than echoing the schema
        augmented_system = (
            f"{system_prompt}\n\n"
            "INSTRUCTIONS: You must populate and return a single valid JSON object with actual values "
            "satisfying all required fields. "
            "Do NOT return the schema, field descriptions, or schema definitions. "
            "Do NOT output markdown commentary or text outside the JSON object."
        )
        messages = [
            {"role": "system", "content": augmented_system},
            {"role": "user", "content": user_prompt},
            *(feedback or []),
        ]

        # Ollama drops the *start* of an over-long prompt (the system instructions)
        # without any error, which is what produced schema-less output before.
        est_tokens = (sum(len(m["content"]) for m in messages) + len(json.dumps(schema))) // CHARS_PER_TOKEN
        if est_tokens + num_predict > num_ctx:
            raise LLMRequestError(
                f"Prompt too large for Ollama context window: ~{est_tokens} prompt tokens + {num_predict} output "
                f"tokens exceeds OLLAMA_NUM_CTX={num_ctx}. Increase OLLAMA_NUM_CTX or reduce the context."
            )

        payload = {
            "model": cfg["model"],
            "messages": messages,
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
        }

        endpoint = f"{base_url}/api/chat"
        try:
            response = httpx.post(endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
        except httpx.ConnectError as ce:
            raise LLMUnavailableError(
                f"Cannot connect to Ollama server at {base_url}. "
                "Ensure Ollama is running on the host (e.g. run 'ollama serve')."
            ) from ce
        except httpx.TimeoutException as te:
            raise LLMTimeoutError(
                f"Ollama request timed out after {timeout} seconds. Increase OLLAMA_TIMEOUT or use a faster model."
            ) from te
        except httpx.HTTPStatusError as hse:
            body = hse.response.text[:300]
            if hse.response.status_code == 404 or "not found" in body.lower():
                raise LLMRequestError(
                    f"Ollama model '{cfg['model']}' not found on server. "
                    f"Please run: ollama pull {cfg['model']}"
                ) from hse
            if hse.response.status_code >= 500:
                raise LLMUnavailableError(f"Ollama server error {hse.response.status_code}: {body}") from hse
            raise LLMRequestError(f"Ollama rejected the request ({hse.response.status_code}): {body}") from hse

        data = response.json()
        raw = (data.get("message") or {}).get("content", "").strip()

        prompt_tokens = data.get("prompt_eval_count")
        if isinstance(prompt_tokens, int):
            completion = data.get("eval_count")
            self._last_usage = {"prompt_tokens": prompt_tokens,
                                "completion_tokens": completion if isinstance(completion, int) else None}
        if isinstance(prompt_tokens, int) and prompt_tokens >= num_ctx - 8:
            logger.warning("Ollama prompt filled the whole context window (%d tokens); input may have been truncated.", prompt_tokens)
        if data.get("done_reason") == "length":
            raise LLMOutputError(
                f"Ollama response was cut off at OLLAMA_NUM_PREDICT={num_predict} tokens before the JSON was complete.",
                raw,
            )

        try:
            return parse_structured_output(raw, response_model, schema)
        except LLMOutputError as exc:
            logger.error("Ollama response failed Pydantic validation: %s\nRaw output: %s", exc, raw[:500])
            raise LLMOutputError(f"Ollama response failed Pydantic validation: {exc}", raw) from exc

    # ---- public interface (supports 2-arg and 3-arg invocations) ----

    def _fallback_allowed(self, error: Exception) -> bool:
        if not self.fallback:
            return False
        # _call_provider wraps exhausted retries in RuntimeError; judge by the underlying error.
        cause = error.__cause__ if error.__cause__ is not None else error
        unavailable = _is_transient_error(cause)
        if not (_is_quota_error(error) or _is_quota_error(cause) or (self.fallback_on == "unavailable" and unavailable)):
            return False
        if self.fallback["name"] in PAID_PROVIDERS and not self.allow_paid_fallback:
            logger.error(
                "Not falling back from %s to %s: the fallback is a paid API and LLM_ALLOW_PAID_FALLBACK is false.",
                self.primary["name"], self.fallback["name"],
            )
            return False
        return True

    def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str = None,
        response_model: Type[T] = None,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
    ) -> T:
        """Generate a validated structured response, or raise. Never returns placeholder data.

        max_output_tokens / temperature are currently honored by the OpenRouter provider only.
        """
        opts = {"max_output_tokens": max_output_tokens, "temperature": temperature}
        # Normalize 2-argument calls: generate_structured(prompt, ResponseModel)
        if response_model is None and isinstance(user_prompt, type) and issubclass(user_prompt, BaseModel):
            response_model = user_prompt
            user_prompt = system_prompt
            system_prompt = "You are an AI assistant that produces structured JSON conforming strictly to the requested schema."

        if response_model is None:
            raise ValueError("response_model must be provided as a Pydantic BaseModel class.")

        primary_name = self.primary["name"]

        try:
            return self._call_provider(self.primary, system_prompt, user_prompt, response_model, opts)
        except Exception as primary_err:
            if not self._fallback_allowed(primary_err):
                if _is_quota_error(primary_err) and not self.fallback:
                    logger.error(f"[{primary_name}] Quota exhausted and no fallback provider configured.")
                raise
            fb_name = self.fallback["name"]
            logger.warning(
                "[%s] failed (%s); falling back to %s.", primary_name, _sanitize(str(primary_err), 200), fb_name
            )
            try:
                result = self._call_provider(self.fallback, system_prompt, user_prompt, response_model, opts)
                logger.info(f"Fallback to {fb_name} succeeded.")
                return result
            except Exception as fb_err:
                raise RuntimeError(
                    f"Both providers failed. "
                    f"Primary ({primary_name}): {_sanitize(str(primary_err), 300)}. "
                    f"Fallback ({fb_name}): {_sanitize(str(fb_err), 300)}"
                ) from fb_err
